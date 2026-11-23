# 📡 API Stream Deck (`/api/deck`)

← [Retour au README](../README.md)

MixPanel peut être piloté depuis le réseau local par [py-livedeck](https://github.com/lfpoulain/py-livedeck), un script ou
tout outil HTTP. Il expose l'**API Stream Deck commune à l'écosystème**, celle d'orchestream-v2 : le serveur décrit
lui-même chaque touche et pousse les changements en direct. Une scène ou une macro créée dans MixPanel apparaît donc
toute seule sur le Stream Deck.

On n'y trouve que ce qui est propre à MixPanel : **scènes, macros, ducking, page A / B, verrouillage et mode de la
X-Touch**. Les sources vMix se pilotent déjà directement depuis LiveDeck.

## Ouvrir l'API

**Réglages → Pilotage à distance (Stream Deck)** : cocher *Ouvrir l'API locale*, choisir le port (**5085** par
défaut) et décocher *Ce poste uniquement* pour la rendre joignable depuis le réseau. L'adresse exacte est affichée
sous les réglages.

L'API est **fermée par défaut**. Ces réglages sont communs à tous les profils.

### Jeton (facultatif)

- **Vide** : l'API est ouverte. À n'ouvrir au réseau que s'il est de confiance.
- **Rempli** (bouton *Générer*) : toutes les routes exigent l'en-tête `Authorization: Bearer <jeton>`. Un jeton placé
  dans l'URL n'est jamais lu. Sans le bon jeton : `401 INVALID_TOKEN`.

## Routes

```
GET  /api/deck/state                     -> état complet, avec commands et keys
GET  /api/deck/events                    -> text/event-stream : state, puis patch
GET  /api/deck/commands                  -> commandes, avec les valeurs de leurs arguments
POST /api/deck/commands/<id>  { "args": { "<nom>": "<valeur>" } }  -> { ok, message, data }
```

Un pupitre n'a besoin que de deux appels : **`/api/deck/events`** pour dessiner les touches et
**`POST /api/deck/commands/<id>`** pour les lancer, avec la commande et les arguments que donne la touche.
Le corps peut être vide pour une commande sans argument.

## État

`GET /api/deck/state` renvoie, en plus de `commands`, `keys` et `updatedAt` :

| Section | Contenu |
|---|---|
| `token` | `{"name": "jeton API" \| "accès libre", "permissions": []}` |
| `app`, `profile` | Nom et version de MixPanel, nom du profil chargé |
| `vmix` | `connected`, `version` |
| `xtouch` | `connected`, `port`, `mode` (`standard` ou `mc`) |
| `page`, `locked` | Page affichée (`A` / `B`), verrouillage |
| `ducking` | `enabled`, `active` (musique baissée), `configured` (micros et sources choisis) |
| `scenes` | `[{id, name, active}]` : `active` = la console est dans cette scène |
| `macros` | `[{id, name, steps, running}]` |

## Touches

```json
{
  "id": "scene.recall:3f9a1c2b",
  "group": "Scènes",
  "command": "scene.recall",
  "args": { "scene": "3f9a1c2b" },
  "title": "Jeu",
  "subtitle": "En place",
  "active": true,
  "tone": "success",
  "available": true,
  "reason": null
}
```

| Touche (`id`) | Rubrique | Ce qu'elle affiche |
|---|---|---|
| `status.vmix` | État | Version de vMix ou « Déconnecté » (affichage seul, `command: null`) |
| `status.xtouch` | État | « Connectée », « Mode Mackie (en pause) » ou « Déconnectée » (affichage seul) |
| `scene.recall:<scène>` | Scènes | Nom de la scène ; allumée (verte) quand la console y est ; grisée sans vMix |
| `macro.run:<macro>` | Macros | Nombre d'étapes, « En cours » pendant l'exécution ; grisée si la macro est vide |
| `ducking.toggle` | Automatisation | « Activé », « Musique baissée » ou « Désactivé » ; grisée tant qu'il n'est pas configuré |
| `page.toggle` | Console | Page affichée |
| `lock.toggle` | Console | « Verrouillé » (rouge) ou « Déverrouillé » |
| `console.modeToggle` | Console | « Standard » ou « Mackie (MC) » ; grisée sans X-Touch |

- `id` est **stable** : c'est l'identifiant interne de la scène ou de la macro, qui ne change pas quand on la renomme.
- `tone` : `neutral`, `success`, `info` (macro en cours), `warning`, `danger`, `accent` (page B).
- `available` / `reason` : l'appui doit marcher maintenant, sinon pourquoi. MixPanel revérifie à l'appui.

## Commandes

| Commande | Argument |
|---|---|
| `scene.recall` | `scene` |
| `scene.update` (enregistre l'état actuel dans la scène) | `scene` |
| `macro.run` | `macro` |
| `ducking.start` / `.stop` / `.toggle` | |
| `page.set` | `page` (`A` ou `B`) |
| `page.toggle` | |
| `lock.start` / `.stop` / `.toggle` | |
| `console.standard` / `.mackie` / `.modeToggle` | |

Exemple :

```bash
curl -X POST http://127.0.0.1:5085/api/deck/commands/scene.recall \
     -H "Content-Type: application/json" -d '{"args": {"scene": "3f9a1c2b"}}'
```

```json
{ "ok": true, "message": "Scène « Jeu » rappelée", "data": { "...": "état à jour" } }
```

## Flux en direct

```
event: state
data: {...état complet...}

event: patch
data: {"changed": {"page": "B"}, "keys": [{...touche modifiée...}], "removedKeys": [], "updatedAt": "..."}

: keepalive
```

L'état est relu chaque seconde et aussitôt après une commande ; un `patch` ne part que s'il y a un changement.
Un commentaire `: keepalive` part au moins toutes les 25 s. 16 flux au plus peuvent être ouverts en même temps
(`429 TOO_MANY_STREAMS` au-delà).

## Erreurs

Format `{"error": {"code", "message", "details?"}}`, avec un message en français affichable tel quel.

| Statut | Code | Quand |
|---|---|---|
| 401 | `INVALID_TOKEN` | Jeton absent ou incorrect |
| 404 | `COMMAND_NOT_FOUND` | Commande inconnue |
| 404 | `ROUTE_NOT_FOUND` | Route inconnue |
| 405 | `METHOD_NOT_ALLOWED` | Mauvais verbe |
| 409 | `REFUSED` | Refus motivé : vMix déconnecté, macro vide, ducking non configuré, X-Touch absente |
| 422 | `VALIDATION_FAILED` | Argument manquant ou invalide, scène ou macro introuvable (`details: {"args.<nom>": "..."}`) |
| 503 | `BUSY` | MixPanel n'a pas répondu dans les 10 s |
