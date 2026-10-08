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
La révision 2 est celle de la fin de la seconde revue de PAT-123 : un ensemble de répertoires candidats, un `cd` cru seulement
dans une grammaire de confiance et sur preuve du résultat (voir « Révision 2 » du lanceur, hypothèses H1 à H9). La règle n'a pas
été ajustée pour obtenir ce tableau : il a été produit une fois la règle fixée par ses tests.

| # | Enregistrement | Enregistré | Révision 2 | Classe |
| --- | --- | --- | --- | --- |
| 1 | A PR 30, correcteur (tour 1) | `/` (`find /`) | **gardé** : excursion réelle | `flag_kept` |
| 2 | L PR 30, implémenteur (tour 0) | racine de travail (`find <racine>`) | **gardé** : excursion réelle | `flag_kept` |
| 3 | A PR 83, implémenteur (tour 0) | `<racine>/tests/…` | retiré : le `cd` d'un appel précédent, au résultat propre, est cru ; les `cd` relatifs refaits ensuite et ratés (ligne `cd:`) ne font qu'ajouter des candidats plus profonds ; depuis chacun le chemin reste dans le bundle | `flag_removed` |
| 4 | L PR 83 (tour 1), session du relecteur | `<racine>/scratch/stderr.log` | **ne disparaît pas tel quel** : le chemin d'origine n'est plus relevé, mais la session du relecteur est relevée (`/<unknown-working-directory>`) pour `cd $T/…` après `T=$(mktemp -d)`, cible que l'audit ne sait pas placer | `flag_moved_to_review` |
| 5 | L PR 27, correcteur (tour 2) | `<racine>/tests/…` | retiré : le dossier repris de l'appel précédent est cru ; le `cd` de l'appel lui-même ne l'est pas (statut non nul accepté par l'hôte) et le chemin reste dans la zone depuis les deux candidats | `flag_removed` |
| 6 | A PR 24 (tour 2), session du relecteur | `<racine>/scratch…` (3 chemins) | retiré | `flag_removed` |
| 7 | A PR 48, correcteur (tour 2) | `<racine>` et `<racine>/scratch` | retiré : après un `cd` raté (ligne `cd:`) deux candidats sont gardés ; `cd ../..` puis `..` et `../scratch` restent dans la zone depuis chacun | `flag_removed` |
| 8 | L PR 48, exploration locale | 2 chemins et leur écho dans 2 résultats | plus de lecture ; **2 chemins inexistants** consignés à part | `flag_moved_to_not_found` |

**Un drapeau est ajouté** : A PR 27 tour 1 (`flag_added`), enregistré propre (`accepted`), dont la session du **relecteur** fait
le même geste (`cd $B/…` après `B=$(mktemp -d)`) et est relevée (`/<unknown-working-directory>`) ; la session du bras reste propre.

Les **23 autres** enregistrements, propres à l'enregistrement, le restent (`clean`) : A PR 30 tour 0 ; L PR 30 exploration ;
L PR 83 exploration et tour 0 ; A PR 27 tour 0 ; L PR 27 exploration, tours 0 et 1 ; A PR 24 tours 0 et 1 ; L PR 24
exploration, tours 0, 1 et 2 ; A PR 48 tours 0 et 1 ; A PR 19 tours 0, 1 et 2 ; L PR 19 exploration, tours 0, 1 et 2 (la liste
clé par clé est dans le JSON).

Bilan honnête : 8 drapeaux enregistrés → **2 gardés** (1 et 2), **4 retirés** (3, 5, 6, 7), 1 **déplacé vers une autre cause**
(4 : plus le chemin d'origine mais un `cd` que l'audit ne sait pas placer, dans la session du relecteur) et 1 **déplacé à part**
(8, chemins inexistants) ; plus **1 ajouté** (A PR 27, relecteur). Des 5 faux drapeaux du diagnostic (3, 4, 5, 6, 7), **4
disparaissent** et 1 (le 4) revient sous une autre forme. Le résumé du rejeu compte `flagged_now: 2` (sessions des bras) et, à
part, `reviewer_flagged_now: 2` (le 4 et A PR 27) : sous la règle B2, ces deux relecteurs relevés laisseraient l'essai
*indécidé* dans une campagne future ; rien n'est recalculé ici.

Ce tableau a les **mêmes classes** que celui de la révision 2 d'avant la seconde revue : la règle a été refaite (liste
blanche, hypothèses écrites, repli pour un hôte non observé, commande exécutée hors zone relevée) sans changer le classement
d'aucun des 32 enregistrements. Ce qui est nouveau dans le JSON : `reviewer_flagged_now`, et `host_models` (30 flux
`claude-code-2.1.285`, 6 flux du harnais local `unverified`, audités sous le repli strict : aucun dossier repris, aucun `cd`
cru ; cela ne change la classe d'aucun enregistrement local ici). Le répertoire `mktemp -d` n'est **pas** modélisé (décision et
raison dans le lanceur : un lien symbolique posé dans ce répertoire ne serait pas vu) ; c'est le prix des deux relecteurs
relevés.

Mesure de ce que le changement de la v4 explique ([`pat-19-audit-replay-v4-scope.json`](pat-19-audit-replay-v4-scope.json),
même rejeu avec `--work-root-not-sensitive`, révision 1 inchangée) : sans la racine de travail dans la liste sensible, la
révision 1 ne relève plus A PR 83, L PR 83, L PR 27 ni L PR 48 (4 enregistrements), relève encore `<racine>` seule pour A PR 48
(la partie `<racine>/scratch` disparaît) et relève toujours A PR 24 (3 chemins), A PR 30 et L PR 30. Les fidélités y sont
« mismatch » par construction (l'enregistré, lui, avait la racine sensible).

Le drapeau 8 : le modèle local a tapé un chemin faux d'un caractère, l'outil a répondu `Path not found: <ce chemin>` (erreur) et
rien n'a été lu. Il n'est pas compté comme une lecture mais n'est pas effacé : il est consigné à part (`new.not_found` du rejeu,
`audit.not_found` de l'enregistrement d'une campagne future) et a sa classe. `not_found` dit « le bras a nommé ce chemin », non
« le bras a visé la zone interdite » : trois chemins inexistants de l'exploration de L PR 83 sont dans sa propre racine privée.
La reconnaissance est étroite (voir le lanceur).

L'imputation au relecteur et B2 : sur la v4, les flux ne passent par cette branche qu'à travers les deux relecteurs relevés
ci-dessus ; la règle (un relecteur relevé ne décide rien, quel que soit son verdict, ne nourrit aucun correcteur) est démontrée
par les tests sur bras factices (PASS et BLOCK), non par la campagne.

## Limites du rejeu (dites, non cachées)

- La liste des racines sensibles est reconstruite avec le dépôt, le dossier d'état et le répertoire personnel **d'aujourd'hui**
  (pas ceux du jour de la campagne) ; les littéraux de base viennent de l'extraction courante, non de chaque bundle ; le refus du
  bac à sable local n'est pas reconstruit. La fidélité de 32 sur 32 limite ce risque pour cette campagne, sans le supprimer pour
  une autre.
- Les rôles sont déduits de l'ordre : la première session cloud d'un enregistrement est prise pour celle du bras, les suivantes
  pour celles du relecteur (vrai pour les chemins A et L, faux pour le chemin C ou un enregistrement repris).
- La révision 2 suppose le comportement observé de Claude Code (version 2.1.285 dans cette campagne) et huit autres
  hypothèses écrites (H1 à H9 du lanceur) ; plusieurs ne sont **pas** montrées par les flux (options et alias du shell de
  l'hôte, complétude d'un résultat long) : elles sont supposées, et dites.
- Le contexte d'un flux (bundle, dossier d'essai) est le `cwd` qu'il annonce ; un flux absent laisse l'enregistrement
  `unavailable`, jamais propre.
- L'audit reste « au mieux » : un chemin construit à l'exécution ou lu par un script n'est pas vu, avant comme après.
- Le rejeu dit ce que l'audit réparé aurait relevé ; il ne dit pas ce que la campagne aurait donné avec lui (verdict du juge,
  revue, tâches indécidées restent inconnus).
- Aucun contenu de flux, aucune sortie de `find /` et aucun nom du répertoire personnel n'est dans le résultat : tout chemin
  sous le répertoire personnel y est écrit `~/<hidden>` (le lanceur refuse d'écrire le résultat s'il en contient un).

## Statut documentaire (AGENTS.md R5)

Ajouté : ce document, son JSON et la mesure `-scope` ; section « Révision 2 de l'audit » et « Rejeu hors ligne » de
[`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) (verbe `replay-audit`, clé `isolation.audit_revision`, constantes
`AUDIT_REVISION` et `FROZEN_PROTOCOLS`, exclusions de capture `_PATCH_EXCLUDES_V2`, champs `audit`, `review.contamination` et
`informative_arms`) ; CHANGELOG. Aucun verbe de `foundry_cli.py`, table de routage ou constante de routage modifiés. Détecteur
FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.
