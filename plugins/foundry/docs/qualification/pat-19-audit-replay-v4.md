# PAT-19 — Rejeu hors ligne de l'audit de contamination sur la campagne v4

PAT-123. Résultat brut : [`pat-19-audit-replay-v4.json`](pat-19-audit-replay-v4.json) (schéma
`foundry.local-first-audit-replay.v1`). Cadre : PAT-ADR-0015 (rien n'est recalculé après coup, une donnée absente n'est
jamais zéro), FOUNDRY-ADR-0010 (aucun appel cloud, aucun modèle chargé). Ce document ne modifie ni le protocole v4, ni sa
configuration, ni les résultats de PAT-122 : les 8 drapeaux restent comptés tels que la règle gelée les a comptés, aucun
verdict, aucun résultat, aucun rapport n'est recalculé.

## Ce qui a été fait

`python3 -m foundry.local_first_runner replay-audit` (voir « Rejeu hors ligne » dans
[`pat-19-launcher-v1.md`](pat-19-launcher-v1.md)) lit les 32 enregistrements, le registre et les **36 flux bruts** de la campagne
`pat-19-x4compare-1` (qui restent hors dépôt ; le `sha256` de chacun, identique à [`streams-manifest.json`](pat-19-runs/x4compare-1/streams-manifest.json),
est dans le résultat) et applique à chaque flux l'audit **révision 1** (celui qui a tourné) et l'audit **révision 2** (réparé).
Il ne lance aucun pilote, aucun modèle, aucun appel cloud ; il lit des fichiers et appelle `git` en lecture seule.

## Ce que montre le rejeu

Fidélité : pour les 32 enregistrements, la révision 1 rejouée retrouve exactement la liste de chemins enregistrée (0 écart).

| # | Enregistrement | Enregistré | Révision 2 |
| --- | --- | --- | --- |
| 1 | A PR 30, correcteur (tour 1) | `/` (`find /`) | **gardé** : excursion réelle |
| 2 | L PR 30, implémenteur (tour 0) | racine de travail (`find <racine>`) | **gardé** : excursion réelle |
| 3 | A PR 83, implémenteur (tour 0) | `<racine>/tests/…` (`../../../tests/…` depuis un sous-dossier) | retiré : fichier du bundle du bras |
| 4 | L PR 83 (tour 1), session du relecteur | `<racine>/scratch/stderr.log` | retiré : dossier de travail réel |
| 5 | L PR 27, correcteur (tour 2) | `<racine>/tests/…` | retiré : fichier du bundle du bras |
| 6 | A PR 24 (tour 2), session du relecteur | `<racine>/scratch…` (3 chemins) | retiré : dossier de travail réel |
| 7 | A PR 48, correcteur (tour 2) | `<racine>` et `<racine>/scratch` (`cd ../..; ls ..; ls ../scratch`) | retiré : dossier de travail hérité du bundle |
| 8 | L PR 48, exploration locale | 2 chemins et leur écho dans 2 résultats | retiré des lectures ; **2 chemins inexistants** consignés à part (`new.not_found`) |

Bilan : 8 drapeaux enregistrés, **2 gardés** (1 et 2), **6 retirés** (3 à 7 : les 5 erreurs de l'audit du diagnostic de PAT-122 ; 8 :
faute de frappe sans accès). **Aucun drapeau ajouté** sur les 32 enregistrements ; les 24 autres restent propres.

Le drapeau 8 : le modèle local a tapé un chemin faux d'un caractère, l'outil a répondu `Path not found: <ce chemin>` (erreur) et
rien n'a été lu. Il n'est pas compté comme une lecture (ce n'en est pas une, comme une tentative bloquée par le bac à sable n'en
est pas une) mais il n'est pas non plus effacé : il est consigné à part, dans `new.not_found` du rejeu et dans `audit.not_found` de l'enregistrement
d'une campagne future, pour qu'un lecteur voie que le bras a nommé un chemin de la zone interdite. La reconnaissance est étroite
(voir le lanceur) : un autre texte d'erreur, un résultat qui nomme un autre chemin, une commande Bash qui nomme le chemin, ou un
même chemin lu ensuite avec succès restent des drapeaux.

L'imputation au relecteur (drapeaux 4 et 6) : sur la v4, les deux sont des erreurs de résolution de chemin, que la réparation du
dossier de travail suffit à retirer ; la règle « une session de relecteur ne marque plus l'essai du bras » n'est donc pas
exercée par ces flux et n'est démontrée que par les tests (bras factices), pas par la campagne.

## Limites du rejeu (dites, non cachées)

- La liste des racines sensibles est reconstruite avec le dépôt, le dossier d'état et le répertoire personnel **d'aujourd'hui**
  (pas ceux du jour de la campagne) ; les littéraux de base viennent de l'extraction courante, non de chaque bundle ; le refus du
  bac à sable local n'est pas reconstruit. La fidélité de 32 sur 32 limite ce risque pour cette campagne, sans le supprimer pour
  une autre.
- Le contexte d'un flux (bundle, dossier d'essai) est le `cwd` qu'il annonce ; un flux absent laisse l'enregistrement
  `unavailable`, jamais propre.
- L'audit reste « au mieux » : un chemin construit à l'exécution ou lu par un script n'est pas vu, avant comme après.
- Le rejeu dit ce que l'audit réparé aurait relevé ; il ne dit pas ce que la campagne aurait donné avec lui (verdict du juge,
  revue, tâches indécidées restent inconnus).
- Aucun contenu de flux, aucune sortie de `find /` et aucun nom du répertoire personnel n'est dans le résultat : tout chemin
  sous le répertoire personnel y est écrit `~/<hidden>` (le lanceur refuse d'écrire le résultat s'il en contient un).

## Statut documentaire (AGENTS.md R5)

Ajouté : ce document et son JSON ; section « Révision 2 de l'audit » et « Rejeu hors ligne » de
[`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) (verbe `replay-audit`, clé `isolation.audit_revision`, constantes
`AUDIT_REVISION` et `FROZEN_PROTOCOLS`, exclusions de capture `_PATCH_EXCLUDES_V2`, champs `audit`, `review.contamination` et
`informative_arms`) ; CHANGELOG. Aucun verbe de `foundry_cli.py`, table de routage ou constante de routage modifiés. Détecteur
FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.
