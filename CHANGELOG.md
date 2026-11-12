# Journal des versions

## Non publié

### Instance unique et fermeture
- **Une seule instance** de MixPanel à la fois : relancer l'exe ramène la fenêtre existante au premier plan au lieu d'en ouvrir une seconde (deux instances se disputaient la X-Touch et vMix).
- La croix de la fenêtre **réduit** MixPanel au lieu de le quitter. Pour quitter vraiment : clic droit sur l'icône de la zone de notification → **Quitter**, ou **Ctrl+Q**. Les mises à jour automatiques et l'arrêt de session Windows ferment toujours l'app normalement.

## 1.2.0 — 2026-11-09

### Mise à jour dans l'app
- MixPanel cherche une **nouvelle version** sur GitHub au démarrage (réglable dans **Réglages → Mises à jour**, ou **Vérifier maintenant**). Un bouton « Mise à jour x.y.z » apparaît dans l'en-tête : il télécharge l'installateur, vérifie sa taille et son empreinte SHA-256, ferme MixPanel, installe puis relance l'app. Rien n'est installé sans ton accord, et le bouton est grisé quand l'app est verrouillée.
- Aucun jeton n'est utilisé : la vérification passe par l'API publique de GitHub.

### Licence
- Le dépôt est public, sous licence **PolyForm Noncommercial 1.0.0** : usage, modification et partage libres pour tout usage non commercial (voir `LICENSE`).

## 1.1.0 — 2026-11-05

### Installateur et verrouillage du direct
- Les releases publient maintenant un **installateur** `MixPanel-x.y.z-setup.exe` (installation par utilisateur, sans droits administrateur, raccourcis, désinstallation) à la place de l'exe seul. Les profils restent dans `%APPDATA%\\MixPanel`.
- Nouveau bouton **Verrouiller** (Ctrl+L) : bloque l'édition et tous les clics à l'écran pour éviter les fausses manips en direct. La X-Touch reste active. Le verrou survit à un redémarrage, et le déverrouillage ou la fermeture demandent confirmation.

## 1.0.15 — 2026-11-04

### Ducking : la musique baisse vraiment
- Le ducking détectait bien la voix mais la musique ne baissait pas : il utilisait la fonction vMix `SetVolumeFade`, sans effet sur certaines versions (ou à cause de l'encodage de sa virgule). Les fondus sont maintenant faits par MixPanel lui-même, en `SetVolume` pas à pas (~25 par seconde) : ducking, fondus des scènes et action « Régler une source à un niveau ».
- Bouger le fader d'une source pendant un fondu l'interrompt (la main reprend la main).
- Les virgules ne sont plus encodées (`%2C`) dans les commandes envoyées à vMix.

## 1.0.14 — 2026-10-29

### Anneau LED : témoin de l'appui
- Les appuis d'encodeur n'ont pas de LED : leur état peut maintenant s'afficher sur l'**anneau**. Nouveau réglage **Anneau LED** par encodeur (inspecteur, onglets Rotation / Appui) :
  - *Auto* (par défaut) : curseur si une action de rotation est associée, sinon état de l'appui ;
  - *Curseur* : niveau de la rotation ;
  - *État de l'appui : plein* : tout l'anneau allumé quand l'action est active, éteint sinon ;
  - *État de l'appui : clignotant* : tout l'anneau clignote quand l'action est active ;
  - *Éteint*.
- Une action sans état (transition…) allume l'anneau pendant l'appui. Le réglage est mémorisé par page (A / B).

## 1.0.13 — 2026-10-29

### Encodeurs : curseur fixe uniquement
- L'anneau d'un encodeur dont la source est coupée ne clignote plus : il montrait une LED clignotante en plus du curseur. L'anneau affiche désormais toujours un seul curseur fixe au niveau vMix.

## 1.0.12 — 2026-10-26

### Faders : vMix suit même les gestes très rapides
- **Position finale envoyée deux fois** : au relâchement d'un fader qui a bougé, sa position finale est renvoyée à vMix tout de suite puis 150 ms plus tard, en plus du renvoi tant que vMix ne l'a pas confirmée.
- Si vMix ne confirme vraiment jamais une valeur : écart d'un pas ou moins → le fader ne bouge pas ; écart plus grand → le Moniteur indique la position du fader et celle de vMix en dB, et le fader suit vMix.
- Un contact sans mouvement (doigt posé puis relâché) ne renvoie rien : un réglage fait ailleurs dans vMix n'est pas écrasé.

## 1.0.11 — 2026-10-26

*(La 1.0.10 n'a pas été publiée : un test a échoué sur GitHub. Ce sont les mêmes changements.)*

### Faders : plus de correction au bout de 2 secondes
- La confirmation de vMix comparait sa valeur à la position exacte du fader, alors que la position part arrondie au dixième : selon la position, elle n'arrivait jamais, et au bout de 2 s le moteur suivait vMix. Elle compare maintenant la valeur réellement envoyée.
- Si vMix laisse passer la dernière commande d'une rafale (geste très rapide), MixPanel lui **renvoie la position du fader** tant qu'il ne l'a pas confirmée, au lieu de déplacer le moteur vers une mauvaise valeur.

### Encodeurs : retour au curseur
- La console n'affiche à distance qu'une LED par anneau : la prise de relais avec arc est retirée. L'anneau montre un curseur au niveau vMix, et son dessin local pendant la rotation est réglé en mode *Single* (un point, plus d'arc qui apparaît et disparaît).

## 1.0.9 — 2026-10-26

### Faders : plus de gros rattrapages
- Après un mouvement, le moteur ne suit plus vMix tant que vMix n'a pas **confirmé** la dernière valeur envoyée (2 s maximum). Avant, il faisait confiance à l'état relu après un délai fixe : si vMix n'avait pas encore appliqué la valeur, le moteur repartait vers l'ancienne position puis revenait.

### Encodeurs : l'arc reste affiché
- La console ne sait afficher à distance qu'un **point** sur l'anneau ; seul l'arc qu'elle dessine elle-même montre un niveau. Nouveau mode **Arc + prise de relais** (par défaut) : quand la position de l'encodeur rejoint le niveau vMix, il pilote directement et la console garde son arc. Si le niveau change ailleurs, un point montre le vrai niveau ; tourne vers lui et l'encodeur reprend la main, sans saut.
- L'ancien comportement (point synchronisé, encodeur relatif) reste disponible dans *Réglages → Encodeurs*.

### Diagnostic
- Nouvel onglet **Rafraîchissement** : aller-retour des commandes vMix, durée et fréquence réelle des lectures d'état, messages MIDI reçus et envoyés par seconde, avec des conseils si quelque chose est trop lent.

## 1.0.8 — 2026-10-26

### Robustesse des retours
- Le calcul des retours (moteurs, LED, anneaux) est protégé contrôle par contrôle : une erreur sur un contrôle n'empêche plus l'envoi des autres.
- Les erreurs de lecture de l'état vMix, jusqu'ici silencieuses (la LED restait simplement éteinte), sont signalées une fois dans le Moniteur.

## 1.0.7 — 2026-10-26

### Faders
- **Plus de rattrapage moteur** après un geste très rapide : vMix publie le volume arrondi à 2 décimales, ce qui fait perdre la précision tout en bas de la course (un fader à 8 % est relu « 0 »). Le moteur se « corrigeait » alors de quelques mm. MixPanel garde maintenant la position exacte qu'il a envoyée quand vMix confirme la même valeur à l'arrondi près.

### Diagnostic
- La calibration des LED est retirée.

## 1.0.6 — 2026-10-26

### LED : l'état reste affiché
- Les LED des boutons (envois Master / A / B, on/off…) restaient éteintes : la console gère elle-même ses LED à l'appui et au relâchement, et son état local prenait le dessus. MixPanel renvoie maintenant l'état réel 80 ms **et** 400 ms après chaque appui, puis toutes les 1,5 s (LED seulement, jamais les moteurs).
- Lecture plus tolérante de la liste des bus d'une source dans le XML vMix (`audiobusses`).

## 1.0.5 — 2026-10-26

### Faders : fini les sauts
- **Échelle de volume vMix** : vMix commande le volume en position de fader (`SetVolume` 0-100) mais publie une amplitude dans son XML (fader à 50 % → « 6.25 »). MixPanel lisait cette amplitude comme une position : le moteur partait au mauvais endroit, d'où les sauts, et « 0 % » n'était pas -∞. Tout est maintenant converti (faders, encodeurs, master, bus, casque, scènes, ducking).
- **Coordination des faders motorisés** : un seul maître à la fois. La main a priorité, le moteur suit immédiatement un déplacement dans l'app, et suit vMix avec une tolérance d'un pas (plus de micro-tremblements).
- Volumes affichés **en dB** sur les faders (+0.0, -24, -∞), comme dans vMix.
- Les scènes enregistrées avec une version précédente sont converties automatiquement.
- Ducking réglé en dB (« Baisser de -12 dB ») au lieu d'un pourcentage.

### LED
- **Calibrer les LED** (Diagnostic) : chaque rangée s'allume avec une vélocité différente, tu choisis celle qui reste fixe.
- Moniteur : option pour afficher les messages envoyés à la console (LED, moteurs, anneaux).

### Tests
- Le faux vMix reproduit l'échelle réelle (amplitude arrondie à 2 décimales) ; nouveaux tests d'échelle et de coordination des faders.

## 1.0.4 — 2026-10-23

### Mode de la console
- MixPanel remet la X-Touch en **mode MIDI standard** à chaque connexion si un autre logiciel l'a laissée en mode Mackie (option activée par défaut).
- **Réglages → Mode de la console** : boutons *Mode standard* et *Mode Mackie (MC)…* pour piloter un autre logiciel. En mode MC, MixPanel se met en pause : aucune action vMix, aucun retour vers la console. La pastille X-Touch l'indique.
- Une console en mode MC (faders en pitch bend) est signalée dans le Moniteur et le Diagnostic.

## 1.0.3 — 2026-10-22

### Correctif important
- **Les boutons ne déclenchaient rien dans vMix** : l'app appelait `AudioOnOff` et `AudioBusOnOff`, qui n'existent pas. Ce sont désormais `Audio`, `AudioBus` et `MasterAudio`, d'après la référence officielle des fonctions vMix.
- Le faux vMix des tests n'accepte plus que les vraies fonctions vMix, et un test vérifie chaque action de l'app.

### Nouveautés
- **Pages A / B** : les boutons LAYER de la console donnent 16 tranches avec des associations séparées. L'app suit la console, et la console suit l'app.
- **Diagnostic X-Touch** : démonstration des sorties (LED, anneaux, moteurs) et contrôle de chaque entrée, sans rien déclencher dans vMix.
- **LED réaffirmées après chaque appui** : la console modifiait elle-même ses LED (allumée puis éteinte), maintenant c'est l'état réel qui s'affiche.
- **Anneaux clignotants** quand la source d'un encodeur est coupée.
- **Pédale d'expression** : le bruit d'une prise sans pédale est ignoré.

## 1.0.2 — 2026-10-21

### Correspondance MIDI fidèle à la console
- Retours conformes à la table officielle Behringer (« RX MIDI Data ») : la X-Touch n'utilise pas les mêmes numéros dans les deux sens.
  - LED des boutons sur les notes 0-38 : elles s'allument enfin au bon endroit.
  - Anneaux LED sur les CC 26-41 (valeurs 0-13), avec un mode d'affichage adapté à chaque action (Fan, Pan…).
  - Capteurs tactiles des faders reconnus (CC 101-109, et non des notes).
- **Faders motorisés** : les retours partent sur le **canal global** de la console. Nouveau réglage *Canal global*, avec un assistant **Détecter…**.
- Vélocités LED par défaut corrigées (2 = allumée, 3 = clignote), avec un préréglage *Manuel Behringer* (1 / 2). Les anciens profils sont convertis automatiquement.
- Layer A sélectionné à la connexion, et état complet renvoyé à la console.
- Encodeurs relatifs : ils partent du niveau actuel de vMix (plus de saut) et continuent en butée.
- Une macro en cours fait clignoter son bouton.
- Apprentissage d'un fader : la position et le capteur tactile sont appris en un seul geste.

## 1.0.1 — 2026-10-21

### Configuration conservée lors des mises à jour
- Les profils et les préférences sont maintenant rangés dans `%APPDATA%\MixPanel`, et non plus à côté de l'exe : remplacer l'exe par une nouvelle version garde toute la configuration. Les sources et l'exe partagent le même dossier.
- Au premier lancement, les profils d'une version précédente (dossier `profiles` à côté de l'exe ou du projet) sont copiés automatiquement, sans rien écraser.
- *Réglages → Profils → Ouvrir* ouvre le dossier des profils.
- La variable d'environnement `MIXPANEL_HOME` permet de choisir un autre dossier (clé USB…).

## 1.0.0 — 2026-10-21

Première version.

### Surface et associations
- Réplique interactive de la X-Touch Compact, fidèle à la console : 3 lignes de boutons, faders motorisés avec bouton dessous, colonne MAIN avec LED d'état et tally PGM/PVW, bloc de droite (8 encodeurs, transport, LAYER).
- Une tranche entière s'assigne en glissant une source dessus. *Suivre la X-Touch* sélectionne le contrôle touché sur la console.
- Apprentissage MIDI par contrôle, capteurs tactiles compris.
- Import des shows Central Control (`.ctrl`).

### Performances
- Fusion des commandes de fader, IPv4 forcé pour `localhost`, lecture XML limitée.
- Correction du fader à l'écran qui restait figé pendant les mouvements rapides.
- Latence vMix affichée dans l'en-tête.

### Encodeurs
- Double fonction rotation / appui.
- Nouvelles actions : balance, gain, navigation de la preview, effet et durée de transition (encodeurs crantés avec recentrage).
- Anneaux LED en vu-mètre (action dédiée ou automatique sur les encodeurs libres).

### Distribution
- Exe Windows autonome et portable (`MixPanel-x.y.z-win64.exe`), compilé et publié automatiquement par GitHub Actions à chaque tag de version.
- Les profils et les shows personnels ne sont pas versionnés : le dépôt n'utilise que des données de démonstration.

### Automatisations
- Scènes audio : capture, rappel avec fondu, LED de scène active.
- Macros : actions et pauses, éditeur dédié, imbrication limitée.
- Ducking automatique, réglable, désactivé par défaut.
- Sources retrouvées automatiquement par leur nom.
