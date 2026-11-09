"""Tests de bout en bout : faux vMix + fausse X-Touch.

    python -m pytest tests      ou      python tests/test_core.py
"""
import sys
import threading
import time
from pathlib import Path

import mido
from PySide6.QtCore import QCoreApplication, QObject, Signal

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import fake_vmix  # noqa: E402

fake_vmix.VERBOSE = False
from pymixpanel.engine import Engine  # noqa: E402
from pymixpanel.importer import import_ctrl  # noqa: E402
from pymixpanel.vmix import VmixClient  # noqa: E402

PORT = 8199
MIC1 = "a81d47c9-2e3b-4f60-8d95-1c7e6b0a4d28"
MIC2 = "c5e92b10-7f4a-4d3c-b816-9a2f0e5d7c41"
MUSIC = "6b0f8e37-41d2-4c9a-a7e5-3d18c2f6b905"
FIXTURE = ROOT / "tests" / "fixtures" / "demo-show.ctrl"
app = QCoreApplication.instance() or QCoreApplication([])


class FakeMidi(QObject):
    message = Signal(object)
    status_changed = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.sent = []

    def configure(self, *_):
        pass

    def send(self, msg):
        self.sent.append(msg)


def wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


_server = None


def setup_engine(tmp_path) -> tuple[Engine, FakeMidi]:
    global _server
    if _server is None:
        _server = fake_vmix.Server(("127.0.0.1", PORT), fake_vmix.Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    prof, _ = import_ctrl(FIXTURE)
    prof.vmix_port, prof.hold_ms = PORT, 150
    prof.path = Path(tmp_path) / "p.json"
    midi = FakeMidi()
    vmix = VmixClient("127.0.0.1", PORT, 50)
    eng = Engine(prof, midi, vmix)
    vmix.start()
    assert wait(lambda: eng.state.online), "pas de connexion au faux vMix"
    return eng, midi


def cc(n, v):
    return mido.Message("control_change", channel=0, control=n, value=v)


def note(n, v=127):
    return mido.Message("note_on", channel=0, note=n, velocity=v)


def test_import_central_control():
    prof, _ = import_ctrl(FIXTURE)
    assert len(prof.mappings) == 23
    assert prof.mappings["fader1"] == {"action": "volume", "input": MIC1, "input_title": "Micro Animateur"}
    assert prof.mappings["push9"]["effect"] == "Cube"


def test_fader_and_feedback(tmp_path):
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    # fader 1 physique -> volume vMix
    eng.on_midi(cc(1, 64))
    assert wait(lambda: abs(inputs[MIC1]["volume"] - 50.4) < 0.2), inputs[MIC1]["volume"]
    # vMix change le volume de Micro Invité -> le moteur du fader 2 suit
    with fake_vmix.LOCK:
        inputs[MIC2]["volume"] = 25.0
    assert wait(lambda: any(m.type == "control_change" and m.control == 2 and m.value == 32
                            for m in midi.sent))
    # bouton fader 1 -> mute, LED éteinte
    eng.on_midi(note(40))
    eng.on_midi(note(40, 0))
    assert wait(lambda: inputs[MIC1]["muted"])
    # LED du bouton de fader 1 = note de retour 24 (et non 40, la note qu'il envoie)
    assert wait(lambda: any(m.type == "note_on" and m.note == 24 and m.velocity == 0 for m in midi.sent))
    # ligne 2 / tranche 1 -> envoi de Micro Animateur sur le bus A
    eng.on_midi(note(24))
    assert wait(lambda: "A" in inputs[MIC1]["buses"])
    assert wait(lambda: eng.leds.get("m2_1") is True)
    # encodeur 9 appuyé -> transition Cube (preview <-> programme)
    before = fake_vmix.S["active"], fake_vmix.S["preview"]
    eng.on_midi(note(8))
    assert wait(lambda: (fake_vmix.S["active"], fake_vmix.S["preview"]) == before[::-1])
    eng.vmix.stop()


def test_touch_protection(tmp_path):
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    eng.on_midi(cc(101, 127))                  # doigt posé sur le fader 1 (capteur = CC 101)
    midi.sent.clear()
    with fake_vmix.LOCK:
        inputs[MIC1]["volume"] = 10.0
    wait(lambda: False, 0.4)
    assert not any(m.type == "control_change" and m.control == 1 for m in midi.sent)
    eng.on_midi(cc(101, 0))                    # doigt relâché -> le moteur suit
    assert wait(lambda: any(m.type == "control_change" and m.control == 1 and m.value == 13
                            for m in midi.sent))
    eng.vmix.stop()


def test_learn_and_strip(tmp_path):
    eng, _ = setup_engine(tmp_path)
    eng.start_learn("enc1")
    eng.on_midi(cc(60, 10))
    assert eng.controls["enc1"].midi.num == 60
    assert eng.profile.midi_overrides["enc1"]["midi"]["num"] == 60
    eng.assign_strip(7, MIC2)
    m = eng.profile.mappings
    assert m["fader8"]["action"] == "volume" and m["fbtn8"]["action"] == "audio_toggle"
    assert [m[f"m{r}_8"]["bus"] for r in (1, 2, 3)] == ["M", "A", "B"]
    eng.vmix.stop()


def test_fast_fader_sweep_has_no_lag(tmp_path):
    """Un balayage rapide (≈ 250 messages/s) doit arriver dans vMix sans retard accumulé."""
    eng, _ = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    for k in range(250):
        eng.on_midi(cc(2, k % 128))
        app.processEvents()
        time.sleep(0.004)
    eng.on_midi(cc(2, 127))
    t0 = time.monotonic()
    assert wait(lambda: inputs[MIC2]["volume"] == 100.0, 1.0)
    # quelques dizaines de ms en local ; marge pour les machines de CI plus lentes.
    # Le défaut corrigé (connexion IPv6) faisait perdre ~2 s.
    assert time.monotonic() - t0 < 0.5, f"lag {time.monotonic() - t0:.3f}s"
    eng.vmix.stop()


def test_encoder_rotation_and_push(tmp_path):
    eng, midi = setup_engine(tmp_path)
    S = fake_vmix.S
    # rotation de l'encodeur 1 = balance, appui = mute (deux fonctions sur le même encodeur)
    eng.set_mapping("enc1", {"action": "balance", "input": MIC1})
    eng.set_mapping("push1", {"action": "audio_toggle", "input": MIC1})
    eng.on_midi(cc(10, 64))                    # première valeur : référence
    eng.on_midi(cc(10, 127))                   # +63 crans -> balance tout à droite
    assert wait(lambda: S["inputs"][MIC1]["balance"] == 1.0)
    muted = S["inputs"][MIC1]["muted"]
    eng.on_midi(note(0))
    assert wait(lambda: S["inputs"][MIC1]["muted"] != muted)
    # encodeur cranté : parcourir la preview ; en butée (127 répété) ça continue
    eng.set_mapping("enc2", {"action": "preview_nav"})
    pvw = S["preview"]
    eng.on_midi(cc(11, 64))
    eng.on_midi(cc(11, 65))
    assert wait(lambda: S["preview"] == pvw % len(S["inputs"]) + 1)
    eng.on_midi(cc(11, 64))
    assert wait(lambda: S["preview"] == pvw)
    eng.on_midi(cc(11, 127))
    eng.on_midi(cc(11, 127))
    assert wait(lambda: S["preview"] != pvw)
    # effet de transition au cran
    eng.set_mapping("enc9", {"action": "transition_effect", "slot": 1})
    eng.on_midi(cc(18, 64))
    eng.on_midi(cc(18, 65))
    assert wait(lambda: S["transitions"][1][0] == "Merge")
    eng.vmix.stop()


def test_relink_by_title(tmp_path):
    eng, _ = setup_engine(tmp_path)
    eng.set_mapping("fader8", {"action": "volume", "input": "ancienne-cle", "input_title": "Musique"})
    assert wait(lambda: eng.mapping("fader8")["input"] == MUSIC)
    eng.vmix.stop()




def test_scene_capture_and_recall(tmp_path):
    eng, _ = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    with fake_vmix.LOCK:
        inputs[MUSIC].update(volume=60.0, muted=False, buses={"M", "A"})
    assert wait(lambda: eng.state.input(MUSIC).volume == 60.0)
    scene = eng.scenes.capture("Jeu")
    eng.set_mapping("rec", {"action": "scene_recall", "scene": scene["id"]})
    assert wait(lambda: eng.leds.get("rec") is True)          # on est dans la scène
    with fake_vmix.LOCK:
        inputs[MUSIC].update(volume=10.0, muted=True, buses={"B"})
    assert wait(lambda: eng.leds.get("rec") is False)
    eng.handle("rec", 1.0)
    eng.handle("rec", 0.0)
    assert wait(lambda: inputs[MUSIC]["volume"] == 60.0 and not inputs[MUSIC]["muted"]
                and inputs[MUSIC]["buses"] == {"M", "A"})
    assert wait(lambda: eng.leds.get("rec") is True)
    assert eng.surface_label("rec")[0] == "Jeu"
    eng.vmix.stop()


def test_macro_steps_and_wait(tmp_path):
    eng, _ = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    with fake_vmix.LOCK:
        inputs[MIC1]["muted"] = False
        inputs[MUSIC]["volume"] = 70.0
    macro = eng.macros.create("Pause")
    macro["steps"] = [{"action": "audio_off", "input": MIC1},
                      {"action": "wait", "ms": 300},
                      {"action": "volume_set", "input": MUSIC, "level": 20, "duration": 0}]
    eng.set_mapping("stop", {"action": "macro", "macro": macro["id"]})
    eng.handle("stop", 1.0)
    assert wait(lambda: inputs[MIC1]["muted"], 1)
    assert inputs[MUSIC]["volume"] == 70.0                    # pas encore : pause de 300 ms
    assert eng.leds.get("stop") is True                        # LED allumée pendant la macro
    assert wait(lambda: inputs[MUSIC]["volume"] == 20.0, 1)
    assert wait(lambda: eng.leds.get("stop") is False)
    eng.vmix.stop()


def test_ducking(tmp_path):
    eng, _ = setup_engine(tmp_path)
    S = fake_vmix.S
    with fake_vmix.LOCK:
        S["inputs"][MIC1].update(muted=False, volume=100.0)
        S["inputs"][MUSIC].update(muted=False, volume=80.0)
        S["meters"][MIC1] = 0.0
    eng.profile.ducking.update(enabled=True, triggers=[{"input": MIC1}], targets=[{"input": MUSIC}],
                               threshold_db=-30, depth_db=-12, hold_ms=200)
    wait(lambda: False, 0.3)
    assert S["inputs"][MUSIC]["volume"] == 80.0
    with fake_vmix.LOCK:
        S["meters"][MIC1] = 0.5                                # quelqu'un parle (≈ -6 dB)
    assert wait(lambda: abs(S["inputs"][MUSIC]["volume"] - 56.6) < 0.2)
    assert eng.ducker.active
    with fake_vmix.LOCK:
        S["meters"][MIC1] = 0.0                                # silence
    assert wait(lambda: S["inputs"][MUSIC]["volume"] == 80.0)
    assert not eng.ducker.active
    # volume changé dans vMix pendant une baisse : il est conservé, pas écrasé au retour
    with fake_vmix.LOCK:
        S["meters"][MIC1] = 0.5
    assert wait(lambda: abs(S["inputs"][MUSIC]["volume"] - 56.6) < 0.2)
    wait(lambda: False, 1.0)
    with fake_vmix.LOCK:
        S["inputs"][MUSIC]["volume"] = 50.0
    assert wait(lambda: MUSIC not in eng.ducker.base)
    with fake_vmix.LOCK:
        S["meters"][MIC1] = 0.0
    assert wait(lambda: not eng.ducker.active)
    wait(lambda: False, 0.3)
    assert S["inputs"][MUSIC]["volume"] == 50.0
    eng.profile.ducking["enabled"] = False
    S["meters"].clear()
    eng.vmix.stop()


def test_ring_meter(tmp_path):
    eng, midi = setup_engine(tmp_path)
    with fake_vmix.LOCK:
        fake_vmix.S["inputs"][MIC1].update(muted=False, volume=100.0)
        fake_vmix.S["meters"][MIC1] = 1.0                      # 0 dB -> anneau plein
    eng.set_mapping("enc16", {"action": "meter", "source": MIC1})
    # anneau de l'encodeur 16 = CC 41, valeur 13 (toutes les LED)
    assert wait(lambda: any(m.type == "control_change" and m.control == 41 and m.value == 13
                            for m in midi.sent))
    fake_vmix.S["meters"].clear()
    eng.vmix.stop()


def test_feedback_uses_official_rx_map(tmp_path):
    """Retours sur le canal global, avec les numéros de la table RX officielle."""
    eng, midi = setup_engine(tmp_path)
    with fake_vmix.LOCK:
        fake_vmix.S["inputs"][MIC1].update(muted=False, buses={"M", "A"})
    eng.profile.global_channel = 2
    midi.sent.clear()
    eng.resend_all()
    out = list(midi.sent)
    assert out and all(m.channel == 1 for m in out)                  # canal global 2
    assert (out[0].type, out[0].control, out[0].value) == ("control_change", 127, 0)  # mode standard
    assert out[1].type == "program_change" and out[1].program == 0   # Layer A
    leds = {m.note: m.velocity for m in out if m.type == "note_on"}
    assert leds[0] == 2       # ligne 1, bouton 1 (Master) : allumée, vélocité 2
    assert leds[8] == 2       # ligne 2, bouton 1 (Bus A)
    assert leds[24] == 2      # bouton du fader 1 : source active
    modes = {m.control: m.value for m in out if m.type == "control_change" and 10 <= m.control <= 25}
    assert modes[10] == 0     # anneau en mode « single » : un curseur, même pendant la rotation
    eng.vmix.stop()


def test_relative_encoder_no_jump(tmp_path):
    """L'encodeur part du niveau actuel de vMix, pas de la position interne de la console."""
    eng, _ = setup_engine(tmp_path)
    with fake_vmix.LOCK:
        fake_vmix.S["inputs"][MIC1]["volume"] = 50.0
    eng.set_mapping("enc3", {"action": "volume", "input": MIC1})
    assert wait(lambda: abs(eng.values.get("enc3", 0) - 0.5) < 0.01)
    eng.on_midi(cc(12, 120))   # la console croit être à 120…
    eng.on_midi(cc(12, 122))   # … +2 crans
    assert wait(lambda: abs(fake_vmix.S["inputs"][MIC1]["volume"] - 52.0) < 0.6)
    eng.vmix.stop()


def test_learn_fader_position_and_touch(tmp_path):
    eng, _ = setup_engine(tmp_path)
    eng.start_learn("fader3")
    eng.on_midi(cc(103, 127))  # le doigt touche d'abord le capteur…
    eng.on_midi(cc(103, 127))
    eng.on_midi(cc(3, 40))     # … puis le fader bouge
    c = eng.controls["fader3"]
    assert (c.midi.num, c.touch.num) == (3, 103) and not eng.learn_target
    eng.vmix.stop()


def test_all_actions_use_real_vmix_functions():
    """Chaque action ne doit appeler que des fonctions qui existent vraiment dans vMix
    (AudioOnOff / AudioBusOnOff n'existent pas : c'était le bug des boutons)."""
    from pymixpanel import actions as A
    from pymixpanel.vmix import BusState, InputState, VmixState
    st = VmixState(online=True, inputs={MIC1: InputState(MIC1, 2, "Micro", "Audio", True)},
                   buses={"A": BusState()}, transitions={1: ("Fade", 500)})
    for a in A.ACTIONS.values():
        if a.engine or a.key.startswith("custom"):
            continue
        p = {**A.defaults(a), "input": MIC1, "scene": "x", "macro": "x"}
        for fn, _, _ in a.run(p, 1 if a.step else 0.5, st):
            assert fn in fake_vmix.REAL_FUNCTIONS, f"{a.key} -> {fn} n'existe pas dans vMix"


def test_layer_b_pages(tmp_path):
    """Layer B de la console = page B de l'app, avec ses propres associations."""
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    eng.set_page("B")
    assert any(m.type == "program_change" and m.program == 1 for m in midi.sent)
    eng.set_mapping("fbtn1", {"action": "audio_toggle", "input": MIC2})
    assert eng.profile.mappings["fbtn1"]["input"] == MIC1          # page A intacte
    eng.set_page("A")
    muted = inputs[MIC2]["muted"]
    eng.on_midi(note(95))                     # bouton de fader 1 en Layer B (note 95)
    eng.on_midi(note(95, 0))
    assert eng.page == "B"                    # l'app a suivi le bouton LAYER de la console
    assert wait(lambda: inputs[MIC2]["muted"] != muted)
    eng.vmix.stop()


def test_led_reassert_after_press(tmp_path):
    """La console change ses LED toute seule à l'appui : l'app réaffirme l'état réel."""
    eng, midi = setup_engine(tmp_path)
    eng.set_mapping("rew", {"action": "recording"})
    wait(lambda: False, 0.2)
    midi.sent.clear()
    eng.on_midi(note(49))                     # appui sur ◀◀ (transport)
    eng.on_midi(note(49, 0))
    leds = lambda: {m.note for m in midi.sent if m.type == "note_on"}  # noqa: E731
    assert wait(lambda: set(range(33, 39)) <= leds())   # tout le groupe transport renvoyé
    eng.vmix.stop()


def test_pedal_noise_and_diagnostic(tmp_path):
    eng, _ = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    with fake_vmix.LOCK:
        inputs[MIC1]["volume"] = 70.0
    eng.set_mapping("exp", {"action": "volume", "input": MIC1})
    eng.on_midi(cc(26, 1))
    eng.on_midi(cc(26, 0))                    # bruit d'une prise sans pédale : ignoré
    wait(lambda: False, 0.3)
    assert inputs[MIC1]["volume"] == 70.0
    eng.on_midi(cc(26, 64))                   # vraie pédale
    assert wait(lambda: abs(inputs[MIC1]["volume"] - 50.4) < 0.2)
    # mode diagnostic : la console est écoutée mais rien ne part vers vMix
    seen = []
    eng.diag_input.connect(lambda cid, role, page: seen.append((cid, role)))
    eng.diagnostic = True
    muted = inputs[MIC1]["muted"]
    eng.on_midi(note(40))
    eng.on_midi(cc(101, 127))
    wait(lambda: False, 0.3)
    assert inputs[MIC1]["muted"] == muted and ("fbtn1", "main") in seen and ("fader1", "touch") in seen
    eng.diagnostic = False
    eng.vmix.stop()


def test_ring_cursor_never_blinks(tmp_path):
    """Source coupée ou non, l'anneau montre un curseur fixe (valeurs 1-13, jamais 14-28)."""
    eng, midi = setup_engine(tmp_path)
    with fake_vmix.LOCK:
        fake_vmix.S["inputs"][MIC1].update(volume=50.0, muted=True)
    eng.set_mapping("enc1", {"action": "volume", "input": MIC1})
    ring = lambda: [m.value for m in midi.sent if m.type == "control_change" and m.control == 26]  # noqa: E731
    assert wait(lambda: ring() and ring()[-1] == 7)   # anneau encodeur 1 = CC 26 ; 50 % -> LED 7
    assert all(v <= 13 for v in ring())
    eng.vmix.stop()


def test_console_mode_mc_and_back(tmp_path):
    """Mode Mackie pour un autre logiciel : MixPanel se met en pause, puis reprend."""
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    midi.sent.clear()
    eng.set_console_mode("mc")
    assert [(m.control, m.value) for m in midi.sent] == [(127, 1)]
    muted = inputs[MIC1]["muted"]
    eng.on_midi(note(40))                     # en MC, la console ne pilote plus vMix
    eng.resend_all()                          # et MixPanel ne lui envoie plus rien
    wait(lambda: False, 0.3)
    assert inputs[MIC1]["muted"] == muted and len(midi.sent) == 1
    eng.set_console_mode("standard")
    assert wait(lambda: any(m.type == "program_change" for m in midi.sent))
    assert any(m.type == "control_change" and m.control == 127 and m.value == 0 for m in midi.sent)
    eng.vmix.stop()


def test_vmix_volume_scale():
    """Le XML vMix donne l'amplitude : 6.25 = fader à 50 % (-24 dB), 24.01 = 70 %."""
    from pymixpanel.vmix import parse_xml, position_to_db
    xml = (b'<vmix><inputs><input key="k" number="1" type="Audio" title="M" volume="6.25" muted="False">'
           b'M</input></inputs><audio><master volume="24.01" muted="False"/></audio></vmix>')
    st = parse_xml(xml)
    assert abs(st.input("k").volume - 50) < 0.01 and abs(st.master.volume - 70) < 0.01
    assert round(position_to_db(50)) == -24 and position_to_db(0) == float("-inf")


def test_fader_coordination(tmp_path):
    """Un seul maître à la fois : la souris déplace le moteur, vMix aussi (à un pas près),
    et rien ne bouge sous le doigt."""
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    motor = lambda: [m.value for m in midi.sent if m.type == "control_change" and m.control == 1]  # noqa: E731
    with fake_vmix.LOCK:
        inputs[MIC1].update(volume=30.0, muted=False)
    assert wait(lambda: motor() and motor()[-1] == 38)
    # déplacement dans l'app -> moteur immédiatement, et vMix suit
    midi.sent.clear()
    eng.handle("fader1", 0.5, from_ui=True)
    assert motor() == [64]
    assert wait(lambda: abs(inputs[MIC1]["volume"] - 50) < 0.1)
    # vMix renvoie 50 (via l'amplitude arrondie) : aucun micro-mouvement du moteur
    wait(lambda: False, 0.6)
    assert motor() == [64]
    # changement dans vMix -> le moteur suit
    with fake_vmix.LOCK:
        inputs[MIC1]["volume"] = 80.0
    assert wait(lambda: motor()[-1] == 102)
    # doigt sur le fader : vMix bouge mais le moteur ne lutte pas
    eng.on_midi(cc(101, 127))
    midi.sent.clear()
    with fake_vmix.LOCK:
        inputs[MIC1]["volume"] = 20.0
    wait(lambda: False, 0.5)
    assert motor() == []
    eng.on_midi(cc(101, 0))
    assert wait(lambda: motor() and motor()[-1] == 25)
    eng.vmix.stop()


def test_lock_blocks_edits_and_ui_but_not_console(tmp_path):
    """Verrouillé : ni édition ni action venant de l'écran, mais la X-Touch continue de piloter vMix."""
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    before = dict(eng.pmap)
    eng.set_locked(True)
    eng.set_mapping("fader1", None)
    eng.assign_strip(2, MIC2)
    eng.clear_strip(0)
    eng.start_learn("fader1")
    assert eng.pmap == before and not eng.learn_target
    vol0 = inputs[MIC1]["volume"]
    eng.handle("fader1", 0.9 if vol0 < 50 else 0.1, from_ui=True)
    wait(lambda: False, 0.4)
    assert inputs[MIC1]["volume"] == vol0
    eng.on_midi(cc(1, 127))   # le fader physique reste actif
    assert wait(lambda: abs(inputs[MIC1]["volume"] - 100) < 0.5)
    eng.set_locked(False)
    eng.set_mapping("fader1", None)
    assert "fader1" not in eng.pmap
    eng.vmix.stop()


def test_update_detection_and_download(tmp_path):
    """Mise à jour : seule une release stable plus récente avec un installateur est proposée,
    et le fichier téléchargé est vérifié (taille, SHA-256)."""
    import hashlib
    import io

    from pymixpanel import updater as U
    base = U.DOWNLOAD_PREFIX + "v9.9.9/MixPanel-9.9.9-setup.exe"
    payload = b"installateur" * 1000
    rel = {"tag_name": "v9.9.9", "body": "notes", "assets": [
        {"name": "MixPanel-9.9.9-setup.exe", "browser_download_url": base, "size": len(payload),
         "digest": "sha256:" + hashlib.sha256(payload).hexdigest()}]}
    up = U.update_from_release(rel, "1.1.0")
    assert up and up.version == "9.9.9" and up.sha256 and up.size == len(payload)
    assert U.update_from_release(rel, "9.9.9") is None and U.update_from_release(rel, "10.0.0") is None
    assert U.update_from_release({**rel, "prerelease": True}, "1.1.0") is None
    assert U.update_from_release({**rel, "assets": []}, "1.1.0") is None
    assert U.update_from_release({**rel, "tag_name": "nightly"}, "1.1.0") is None
    evil = {**rel, "assets": [{**rel["assets"][0], "browser_download_url": "https://evil.example/MixPanel-9.9.9-setup.exe"}]}
    assert U.update_from_release(evil, "1.1.0") is None
    assert U.parse_version("v1.10.0") > U.parse_version("1.9.9")

    class Resp(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}
        __enter__ = lambda self: self  # noqa: E731
        __exit__ = lambda self, *a: None  # noqa: E731

    real = U._open
    try:
        U._open = lambda url, timeout: Resp(payload)
        seen = []
        path = U.download(up, lambda d, t: seen.append((d, t)))
        assert path.read_bytes() == payload and seen[-1] == (len(payload), len(payload))
        U._open = lambda url, timeout: Resp(payload[:-1] + b"x")
        try:
            U.download(up)
            raise AssertionError("une empreinte différente doit être refusée")
        except U.UpdateError:
            pass
    finally:
        U._open = real


def test_bus_led_stays_lit(tmp_path):
    """Source envoyée sur Master / A : sa LED reste allumée, même si la console l'éteint
    d'elle-même au relâchement du bouton (réaffirmation après l'appui + périodique)."""
    eng, midi = setup_engine(tmp_path)
    with fake_vmix.LOCK:
        fake_vmix.S["inputs"][MIC1]["buses"] = {"M", "B"}
    eng.set_mapping("m2_1", {"action": "bus_route", "input": MIC1, "bus": "A"})
    led = lambda n: [m.velocity for m in midi.sent if m.type == "note_on" and m.note == n]  # noqa: E731
    eng.on_midi(note(24))                     # appui : la source part sur le bus A
    eng.on_midi(note(24, 0))
    assert wait(lambda: "A" in fake_vmix.S["inputs"][MIC1]["buses"])
    assert wait(lambda: led(8) and led(8)[-1] == 2)          # LED ligne 2 / bouton 1 allumée
    midi.sent.clear()
    assert wait(lambda: led(8) == [2] or led(8)[-1:] == [2], 2.5)   # renvoyée périodiquement
    assert not any(m.type == "control_change" and m.control <= 9 for m in midi.sent)  # moteurs intacts
    eng.vmix.stop()


def test_no_motor_catch_up_at_bottom(tmp_path):
    """Geste rapide qui finit tout en bas : vMix relit « 0.00 » (précision perdue), mais le
    moteur ne doit pas se « rattraper » de quelques mm au relâchement."""
    eng, midi = setup_engine(tmp_path)
    motor = lambda: [m.value for m in midi.sent if m.type == "control_change" and m.control == 1]  # noqa: E731
    eng.on_midi(cc(101, 127))                 # doigt posé
    for v in (120, 90, 60, 30, 12, 10):       # descente très rapide
        eng.on_midi(cc(1, v))
    eng.on_midi(cc(101, 0))                   # relâché à 10/127 (≈ -83 dB, XML « 0.00 »)
    assert wait(lambda: fake_vmix.S["inputs"][MIC1]["volume"] < 8)
    midi.sent.clear()
    wait(lambda: False, 1.0)
    assert motor() == [], f"rattrapage moteur : {motor()}"
    eng.vmix.stop()


def test_no_catch_up_with_slow_vmix(tmp_path):
    """vMix chargé (volumes appliqués 0,9 s en retard, après le délai de protection) :
    le moteur ne doit pas repartir vers l'ancienne valeur, encore lue dans vMix."""
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    motor = lambda: [m.value for m in midi.sent if m.type == "control_change" and m.control == 1]  # noqa: E731
    with fake_vmix.LOCK:
        inputs[MIC1].update(volume=30.0, muted=False)
    assert wait(lambda: motor() and motor()[-1] == 38)
    fake_vmix.S["apply_delay"] = 0.9
    try:
        eng.on_midi(cc(101, 127))
        for v in (60, 90, 120, 127):
            eng.on_midi(cc(1, v))
        eng.on_midi(cc(101, 0))
        midi.sent.clear()
        assert wait(lambda: inputs[MIC1]["volume"] == 100.0, 3)
        wait(lambda: False, 0.5)
        assert motor() == [], f"rattrapage vers une valeur périmée : {motor()}"
    finally:
        eng.vmix.stop()
        wait(lambda: False, 1.5)               # laisse s'appliquer les volumes différés
        fake_vmix.S["apply_delay"] = 0.0


def test_lost_final_command_is_resent(tmp_path):
    """vMix perd la fin d'une rafale (commandes acquittées mais pas appliquées) : le moteur ne
    « corrige » pas vers l'ancienne valeur, la position du fader est renvoyée à vMix."""
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    motor = lambda: [m.value for m in midi.sent if m.type == "control_change" and m.control == 1]  # noqa: E731
    with fake_vmix.LOCK:
        inputs[MIC1].update(volume=30.0, muted=False)
    assert wait(lambda: motor() and motor()[-1] == 38)
    eng.on_midi(cc(101, 127))
    for v in (60, 90, 120):
        eng.on_midi(cc(1, v))
    fake_vmix.S["ignore_until"] = time.monotonic() + 0.6   # la fin de la rafale est perdue
    for v in (110, 100):
        eng.on_midi(cc(1, v))
    eng.on_midi(cc(101, 0))
    midi.sent.clear()
    assert wait(lambda: abs(inputs[MIC1]["volume"] - 100 / 127 * 100) < 0.2, 3)   # renvoyé, appliqué
    wait(lambda: False, 0.6)
    assert motor() == [], f"le moteur a « corrigé » : {motor()}"
    eng.vmix.stop()


def test_final_position_sent_twice(tmp_path):
    """Au relâchement après un geste, la position finale part deux fois vers vMix."""
    eng, _ = setup_engine(tmp_path)
    seen = []
    real = fake_vmix.function

    def spy(fn, q):
        if fn == "SetVolume" and q.get("Input") == MIC1:
            seen.append(q.get("Value"))
        return real(fn, q)
    fake_vmix.function = spy
    try:
        eng.on_midi(cc(101, 127))
        for v in (20, 70, 120, 90):
            eng.on_midi(cc(1, v))
        eng.on_midi(cc(101, 0))
        final = f"{90 / 127 * 100:.1f}"
        # le renvoi immédiat fusionne avec la commande du geste encore en file ; celui de +150 ms
        # part séparément : la position finale arrive deux fois à vMix
        assert wait(lambda: seen.count(final) >= 2, 1.5), seen
    finally:
        fake_vmix.function = real
    eng.vmix.stop()


def test_ring_shows_push_state(tmp_path):
    """Anneau = témoin de l'appui : plein (27) si l'action est active, éteint (0) sinon,
    clignotant (28) au choix ; une action sans état l'allume pendant l'appui."""
    eng, midi = setup_engine(tmp_path)
    inputs = fake_vmix.S["inputs"]
    ring = lambda: [m.value for m in midi.sent if m.type == "control_change" and m.control == 27]  # noqa: E731
    with fake_vmix.LOCK:
        inputs[MIC2]["muted"] = False
    eng.set_mapping("push2", {"action": "audio_toggle", "input": MIC2})   # rotation libre -> auto = état
    assert eng.ring_mode("enc2") == "state"
    assert wait(lambda: ring() and ring()[-1] == 27)         # source active : anneau plein
    eng.on_midi(note(1))                                      # appui encodeur 2 : coupe la source
    eng.on_midi(note(1, 0))
    assert wait(lambda: ring()[-1] == 0)
    eng.set_ring_mode("enc2", "state_blink")
    eng.on_midi(note(1))
    eng.on_midi(note(1, 0))
    assert wait(lambda: ring()[-1] == 28)                     # réactivée : anneau clignotant
    # action sans état (transition) : l'anneau s'allume pendant l'appui
    eng.set_ring_mode("enc2", "auto")
    eng.set_mapping("push2", {"action": "transition", "effect": "Cut"})
    eng.on_midi(note(1))
    assert wait(lambda: ring()[-1] == 27)
    eng.on_midi(note(1, 0))
    assert wait(lambda: ring()[-1] == 0)
    # une rotation associée repasse l'anneau en curseur
    eng.set_mapping("enc2", {"action": "volume", "input": MIC2})
    assert eng.ring_mode("enc2") == "cursor"
    eng.vmix.stop()


def test_ducking_without_setvolumefade(tmp_path):
    """Même si SetVolumeFade ne fait rien dans vMix, le ducking baisse puis remonte la musique
    (fondu fait par MixPanel, SetVolume pas à pas)."""
    eng, _ = setup_engine(tmp_path)
    S = fake_vmix.S
    seen = []
    with fake_vmix.LOCK:
        S["inputs"][MIC1].update(muted=False, volume=100.0)
        S["inputs"][MUSIC].update(muted=False, volume=80.0)
        S["meters"][MIC1] = 0.0
    S["fade_broken"] = True
    try:
        eng.profile.ducking.update(enabled=True, triggers=[{"input": MIC1}], targets=[{"input": MUSIC}],
                                   threshold_db=-30, depth_db=-12, attack_ms=400, release_ms=400, hold_ms=200)
        wait(lambda: False, 0.3)
        with fake_vmix.LOCK:
            S["meters"][MIC1] = 0.5
        assert wait(lambda: (seen.append(S["inputs"][MUSIC]["volume"]) or True)
                    and abs(S["inputs"][MUSIC]["volume"] - 56.6) < 0.2, 3)
        assert any(57 < v < 79 for v in seen), "pas de fondu progressif"
        with fake_vmix.LOCK:
            S["meters"][MIC1] = 0.0
        assert wait(lambda: abs(S["inputs"][MUSIC]["volume"] - 80.0) < 0.2, 3)
    finally:
        S["fade_broken"] = False
        S["meters"].clear()
        eng.profile.ducking["enabled"] = False
    eng.vmix.stop()


if __name__ == "__main__":
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                fn(d) if fn.__code__.co_argcount else fn()
            print("OK ", name)
