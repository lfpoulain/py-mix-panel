# 🎹 Référence MIDI X-Touch Compact

← [Retour au README](../README.md)

MixPanel suit la **table MIDI officielle** de la X-Touch Compact en mode standard, Layer A (*Quick Start Guide*
Behringer, pages « MIDI Map » et « RX MIDI Data »). Elle est recoupée avec les relevés matériels de la librairie
[jetsonhacks/xtouch-compact](https://github.com/jetsonhacks/xtouch-compact).

> [!IMPORTANT]
> La console **n'utilise pas les mêmes numéros dans les deux sens**. Un bouton qui envoie la note 16 allume sa LED
> avec la note 0. Et **tous les retours** (moteurs, LED, anneaux) doivent arriver sur le **canal global** de la
> console, qui n'est pas forcément le canal 1. Règle-le dans **Réglages → Canal global → Détecter…**.

## Disposition

```
┌────────────────────────────────────────────────────────┬──────┬───────────────────┐
│  ◎ ◎ ◎ ◎ ◎ ◎ ◎ ◎        encodeurs 1-8 (+ appui)        │ LED  │   ◎ 9     ◎ 10    │
│  ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢        ligne 1 (upper top)            │ état │   ◎ 11    ◎ 12    │
│  ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢        ligne 2 (upper mid)            │      │   ◎ 13    ◎ 14    │
│  ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢        ligne 3 (upper bottom)         │      │   ◎ 15    ◎ 16    │
│  ┃ ┃ ┃ ┃ ┃ ┃ ┃ ┃        faders 1-8 (motorisés)         │  ┃   │  ◀◀  ▶▶           │
│  ┃ ┃ ┃ ┃ ┃ ┃ ┃ ┃                                       │ MAIN │  LOOP ●           │
│  ┃ ┃ ┃ ┃ ┃ ┃ ┃ ┃                                       │  ┃   │  ■    ▶           │
│  ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢        boutons de fader (lower)       │  ▢   │  ◀A  LAYER  B▶    │
└────────────────────────────────────────────────────────┴──────┴───────────────────┘
```

## Ce que la console envoie (TX, canal 1)

| Contrôle | Identifiant MixPanel | Message |
|---|---|---|
| Faders 1-8 / principal | `fader1` … `fader8` / `main` | CC 1-8 / CC 9 (0-127) |
| Capteurs tactiles des faders | – | **CC 101-109** (127 = touché, 0 = relâché) |
| Encodeurs 1-16 (rotation) | `enc1` … `enc16` | CC 10-25 (valeur absolue 0-127, tenue par la console) |
| Encodeurs 1-16 (appui) | `push1` … `push16` | Note 0-15 |
| Ligne 1, boutons 1-8 | `m1_1` … `m1_8` | Note 16-23 |
| Ligne 2, boutons 1-8 | `m2_1` … `m2_8` | Note 24-31 |
| Ligne 3, boutons 1-8 | `m3_1` … `m3_8` | Note 32-39 |
| Boutons de fader 1-8 / principal | `fbtn1` … `fbtn8` / `mainbtn` | Note 40-47 / Note 48 |
| ◀◀ ▶▶ LOOP ● ■ ▶ | `rew` `ff` `loop` `rec` `stop` `play` | Note 49-54 |
| Pédale d'expression / footswitch | `exp` / `foot` | CC 26 / CC 27 |

Les boutons **LAYER A / B** n'envoient rien : ils changent de couche à l'intérieur de la console. En Layer B,
tous les numéros sont décalés, et MixPanel s'en sert pour savoir quelle couche est active (pages A / B) :

| Contrôle (Layer B) | Message |
|---|---|
| Faders 1-8 / principal | CC 28-35 / CC 36 |
| Capteurs tactiles | CC 111-119 |
| Encodeurs 1-16 (rotation / appui) | CC 37-52 / Notes 55-70 |
| Lignes 1, 2, 3 | Notes 71-78, 79-86, 87-94 |
| Boutons de fader 1-8 / principal | Notes 95-102 / 103 |
| Transport | Notes 104-109 |
| Pédale d'expression / footswitch | CC 63 / CC 64 |

Les retours (LED, moteurs, anneaux) sont les mêmes pour les deux couches : MixPanel renvoie l'état de la page
active à chaque changement.

## Ce que la console écoute (RX, canal global)

| Retour | Message | Valeurs |
|---|---|---|
| Moteurs des faders 1-8 / principal | CC 1-8 / CC 9 | 0-127, de bas en haut |
| Mode des anneaux, encodeurs 1-16 | CC 10-25 | 0 Single · 1 Pan · **2 Fan** · 3 Spread · 4 Trim |
| Valeur des anneaux, encodeurs 1-16 | **CC 26-41** | 0 = éteint, 1-13 = LED de gauche à droite, 27 = tout allumé |
| LED ligne 1 / 2 / 3 | **Notes 0-7 / 8-15 / 16-23** | vélocité (voir plus bas) |
| LED boutons de fader 1-8 / principal | **Notes 24-31 / 32** | |
| LED transport ◀◀ ▶▶ LOOP ● ■ ▶ | **Notes 33-38** | |
| LED du footswitch | CC 42 | 0-63 éteinte, 64-127 allumée |
| Choix du Layer | Program Change | 0 = A, 1 = B |
| Mode standard / MC | CC 127 | 0 = standard, 1 = MC |

Les appuis d'encodeur n'ont pas de LED.

### Vélocité des LED

Le manuel et le matériel ne sont pas d'accord, d'où le choix dans **Réglages → LED des boutons** :

| Vélocité | Manuel Behringer | Firmware récent (observé) |
|---:|---|---|
| 0 | éteinte | éteinte |
| 1 | allumée | éteinte |
| 2 | clignote | **allumée** |
| 3 | ignorée | **clignote** |

Par défaut, MixPanel utilise le réglage *Firmware récent* : 0 éteinte, 2 allumée, 3 clignote (une macro en cours
fait clignoter son bouton). Si tes LED clignotent au lieu de rester allumées, choisis *Manuel Behringer*.

## Comment MixPanel s'en sert

### Faders motorisés
Un seul « maître » à la fois :
- **La main** : tant que le capteur tactile (CC 101-109) est actif, ou pendant le délai *Protection fader* après un
  mouvement, le moteur ne reçoit rien. Ensuite, le geste fait foi : tant que vMix n'a pas **confirmé** la position
  du fader, MixPanel la lui **renvoie** (toutes les 0,4 s, 3 s maximum) au lieu de déplacer le moteur vers un état
  périmé ou une commande que vMix aurait laissé passer. Au relâchement d'un fader qui a bougé, la position finale
  part en plus deux fois (tout de suite, puis 150 ms plus tard).
- **La souris** (fader déplacé dans l'app) : le moteur suit immédiatement.
- **vMix** (volume changé ailleurs, scène, ducking, fondu) : le moteur suit (CC 1-9 sur le canal global), avec une
  tolérance d'un pas pour éviter les micro-tremblements dus aux arrondis.
- Les mouvements physiques ne sont jamais renvoyés en écho.

La position 0-127 du fader correspond à la position 0-100 du fader vMix (même courbe : 0 dB en haut, -24 dB au
milieu, -∞ en bas). Le XML de vMix donnant une amplitude, MixPanel la reconvertit : `position = 100 × (volume / 100)^¼`.

### Encodeurs
- La console garde sa propre valeur 0-127 pour chaque encodeur et **ne permet pas de la repositionner**. MixPanel
  n'utilise donc que le **sens et le nombre de crans** et les applique au niveau actuel dans vMix : jamais de saut.
- À distance, l'anneau n'allume **qu'une LED** (valeurs 1-13), quel que soit son mode : MixPanel affiche un
  **curseur** au niveau vMix. L'anneau peut aussi servir de **témoin de l'appui** (les appuis n'ont pas de LED) :
  27 = tout allumé, 28 = tout clignotant, 0 = éteint (réglage *Anneau LED* dans l'inspecteur). Le mode de l'anneau (CC 10-25) ne règle que le dessin que la console fait elle-même
  pendant qu'on tourne : MixPanel le met en *Single*, pour que ce bref éclair soit lui aussi un point et non un arc.
  (Même constat dans la librairie de référence jetsonhacks/xtouch-compact.)
- En butée, la console répète 0 ou 127 : MixPanel le compte comme un cran de plus, donc on peut continuer à tourner.
- Un cran = 1 % de la course. Tourner vite envoie des pas de 2 ou 3 : l'accélération est naturelle.

### LED réaffirmées
La console change elle-même ses LED quand on appuie (allumée à l'appui, éteinte au relâchement), et un appui sur
un bouton du transport peut changer la LED d'un autre bouton du groupe. MixPanel renvoie donc l'état réel :
- 80 ms puis 400 ms après chaque appui et chaque relâchement (le bouton, ou tout le groupe transport) ;
- toutes les 1,5 s pour toutes les LED (jamais les moteurs), pour qu'aucune ne reste fausse.

### Pédale d'expression débranchée
Sans pédale, la console envoie du bruit sur CC 26 (valeurs 0 et 1). MixPanel l'ignore tant qu'il n'a pas reçu une
vraie valeur (≥ 4).

### À la connexion
MixPanel sélectionne la couche de la page active (Program Change 0 ou 1), règle le mode de chaque anneau et
renvoie tout l'état : LED, moteurs et anneaux.

## Canal global

C'est le canal sur lequel la console écoute **tous** les retours. Il se règle dans le menu de configuration de la
X-Touch. S'il ne correspond pas, la console reçoit tout mais n'en fait rien : pas de moteur, pas de LED.

**Réglages → Canal global → Détecter…** fait bouger le fader 1 et les LED de la ligne 1 canal par canal. Clique
**Il bouge !** quand la console réagit.

## Mode standard et mode Mackie (MC)

| | Mode standard | Mode Mackie Control (MC) |
|---|---|---|
| Pour | MixPanel, et tout logiciel qui sait apprendre des messages MIDI | Logiciels de musique (Cubase, Reaper, Logic, Ableton…) |
| Contrôles | Tous libres : notes et CC génériques, Layer A / B | Rôles imposés : REC / SOLO / MUTE / SELECT, transport, banques de 8 pistes |
| Faders | CC 0-127 | Pitch bend (annoncé en 14 bits, mais 7 bits utiles sur la Compact) |
| LED « MC » | éteinte | allumée |

MixPanel a besoin du **mode standard**. Le mode se change avec CC 127 sur le canal global (0 = standard, 1 = MC) :

- À chaque connexion, MixPanel **remet la console en mode standard** si un autre logiciel l'a laissée en MC
  (option *Remettre la console en mode standard au lancement*, activée par défaut).
- **Réglages → Mode de la console → Mode Mackie (MC)…** la bascule pour un autre logiciel. Pendant ce temps,
  MixPanel est **en pause** : il ignore la console (aucune action vMix) et ne lui envoie plus rien. La pastille
  X-Touch affiche « mode MC (en pause) ».
- **Mode standard** (ou relancer MixPanel) la ramène et renvoie tout l'état.
- Si la console envoie du pitch bend alors que MixPanel l'attend en standard, le Moniteur et le Diagnostic le signalent.

## Apprentissage MIDI

**Apprendre le MIDI** ne change que ce que la console **envoie** (entrée). Les retours dépendent de la position
physique du contrôle et ne changent pas. Pour un fader, pose le doigt et bouge-le : la position et le capteur
tactile sont appris ensemble.
