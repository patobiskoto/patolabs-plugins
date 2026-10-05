# PAT-19 — Corpus rejouable du protocole v1

PAT-107. Statut : corpus gelé pour le protocole v1
([`pat-19-protocol-v1.md`](pat-19-protocol-v1.md)). Cadre : PAT-ADR-0015, FOUNDRY-ADR-0019
(comparaison bornée, tâches déjà résolues rejouées à leur SHA de base, jugement mécanique,
pas de matrice). Ce ticket ne lance aucun modèle et ne fait aucun appel cloud ni tracker.

Un changement d'un critère, de la graine, de l'algorithme de tirage ou du snapshot ouvre une
version 2 du corpus (comme pour les coordonnées gelées du protocole).

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `pat-19-protocol-v1.md` | Protocole v1 validé, coordonnées gelées, mention « un changement ouvre une version 2 » |
| `pat-19-corpus-snapshot-v1.json` | Entrée figée : les 84 PR mergées (numéro, ticket, SHA de base, SHA fusionné, fichiers avec lignes ajoutées/supprimées, critères d'acceptation, tests protégés) |
| `pat-19-corpus-manual-exclusions-v1.json` | Exclusions de jugement, avec leur raison |
| `pat-19-corpus-manifest-v1.json` | Tirage (graine, algorithme, exclusions par PR), 6 tâches de comparaison + 6 de tamis nominatives, file de remplacement, preuve du juge |
| `../../tooling/foundry/local_first_corpus.py` | Outil (aucune dépendance réseau au rejeu) |
| `../../tests/test_local_first_corpus.py` | Tests déterministes de l'outil et cohérence des fichiers ci-dessus |

## Outil

Depuis `plugins/foundry/tooling` (Python 3, `git` et `pytest` locaux uniquement) :

```
python3 -m foundry.local_first_corpus snapshot --repo <repo> --prs-json <gh.json> [--ref SHA] --ac-overrides <json> --out <snapshot>
python3 -m foundry.local_first_corpus draw   --snapshot <snapshot> --seed <graine> [--manual-exclusions <json>] [--out <json>]
python3 -m foundry.local_first_corpus bundle --repo <repo> --snapshot <snapshot> --pr <n> --dest <dossier>
python3 -m foundry.local_first_corpus judge  --repo <repo> --snapshot <snapshot> --pr <n> --candidate <worktree>
python3 -m foundry.local_first_corpus verify --repo <repo> --snapshot <snapshot> --seed <graine> [--manual-exclusions <json>] --workdir <dossier> --out <manifest>
```

- `snapshot` : `<gh.json>` est la sortie de `gh pr list --state merged --limit 200 --json
  number,title,body,mergeCommit,baseRefOid,headRefOid,baseRefName`. Le SHA de base est le
  parent du commit de squash ; le SHA de tête est le commit de squash fusionné (le SHA de tête de
  la branche de PR est conservé en `pr_head_sha`). Cinq PR (PAT-33, 34, 35, 36, 38) ont été
  fusionnées sur la branche empilée de PAT-22 et non sur `main` : leur squash a un seul parent, elles
  sont rejouables (`merge_kind: squash_on_stacked_branch`).
- `draw` : classe les PR éligibles par `sha256("<graine>:<numéro de PR>")` croissant ; les 6
  premières forment l'ensemble de **comparaison**, les 6 suivantes le **tamis** local
  (protocole section 4), le reste est la file de remplacement ordonnée. Les deux ensembles sont
  disjoints : une tâche de comparaison n'a jamais servi au tamis (vérifié à la finalisation et testé). Rejeu : même snapshot + même graine = même liste (testé).
- `bundle` : worktree git jetable au SHA de base, créé sous `--dest` (hors du checkout de
  développement), sans les tests protégés ; `TASK.md` est l'énoncé du ticket tel qu'écrit dans le
  tracker (titre et corps), octet pour octet (jamais le diff mergé, la description de la PR ni les
  tests protégés). `bundle` refuse (échec fermé) toute PR dont `ac_source` n'est pas
  `tracker_issue`.
- `judge` : voir ci-dessous ; code de sortie 0 si `ACCEPTED`, 1 sinon.
- `verify` : pour chaque tâche tirée, construit deux bundles jetables (base + diff mergé hors
  tests protégés ; base seule), lance le juge sur chacun, écarte la tâche si le juge n'accepte pas
  la solution d'origine ou accepte la base, la remplace par la file de remplacement (un créneau
  défaillant prend la première entrée restante), et consigne la raison.

## Critères du protocole, appliqués aux PR mergées

Chaque PR exclue porte toutes ses raisons dans `draw.excluded` du manifeste ; aucune exclusion
n'est silencieuse. Les critères sont des prédicats explicites
(`exclusion_reasons`) :

- `not_single_parent_squash_commit`, `no_issue_id`, `empty_acceptance_criteria` ;
- `over_300_changed_lines` : lignes ajoutées + supprimées, tous fichiers confondus, au plus 300 ;
- `no_source_code_under_plugins_foundry` / `documentation_only` : au moins un `.py` hors tests sous
  `plugins/foundry/tooling` ou `hooks` ;
- `no_tests_under_plugins_foundry`, `no_protected_tests` : au moins un test ajouté ou modifié
  (critère d'acceptation vérifiable par un test, au sens mécanique) ;
- `topic:gates_evaluation`, `topic:authority`, `topic:data_migration`, `topic:sensitive_concurrency` :
  motif de chemin appliqué aux seuls `.py` de production (`TOPIC_RULES`) ou mot-clé du titre ;
- `manual:<raison>` : exclusion de jugement versionnée dans
  `pat-19-corpus-manual-exclusions-v1.json` (une seule : PAT-102, autorité de la dérogation
  humaine de clôture d'Epic).

Les règles de sujet sont volontairement étroites et lisibles, pas exhaustives : un cas limite se
traite dans la liste manuelle, jamais par un choix non consigné.

## Tests protégés

Source : la PR d'origine. Sont protégés les fichiers de `plugins/foundry/tests/` **ajoutés ou
modifiés** (les suppressions sont ignorées).

- Fichier `tests/test_*.py` ajouté : tout le fichier est protégé (sélection `file`).
- Fichier `tests/test_*.py` modifié : comparaison AST des fonctions de niveau module et des
  méthodes de classe entre le SHA de base et le SHA fusionné ; sont protégées les fonctions
  `test*` ajoutées ou dont l'AST a changé (mise en forme ignorée), sélectionnées par identifiant
  de nœud `tests/test_x.py::Classe::test_y`. Si une fonction d'assistance non `test*` ou une
  instruction de module autre qu'un import a changé, tout le fichier est sélectionné
  (`selection: file`) : les tests préexistants du fichier sont alors rejoués aussi.
- Autre fichier sous `tests/` ajouté ou modifié (`conftest.py`, fixtures, assistants) : type
  `support`, restauré par le juge depuis le SHA fusionné et jamais lancé seul. Dans le bundle, un
  support ajouté est absent ; un support modifié reste à sa version de base.

Dans le bundle, un fichier de test ajouté est supprimé et les fonctions de test modifiées sont
retirées du fichier de base (une classe vidée reçoit `pass`).

## Juge

`judge` prend un worktree candidat : il écrase les chemins protégés avec les versions du SHA
fusionné (le fichier du candidat à ces chemins ne compte donc jamais), restaure `conftest.py` et
`pytest.ini` à la version du SHA fusionné, lance le `pytest` du dépôt uniquement sur les
identifiants sélectionnés (environnement sans variable `FOUNDRY_*`), et rend `ACCEPTED` si et
seulement si pytest sort avec 0, au moins un test passe, et aucun test n'échoue, n'est en erreur ou
ignoré ; sinon `REFUSED`, avec les comptes (réussis, échoués, erreurs, ignorés). Un test écrit par
le candidat dans un autre fichier n'est jamais sélectionné.

Limites connues :

- La sélection est faite au niveau fonction ; elle ne capte pas un changement de comportement d'un
  test causé par une autre modification de module (hors assistants et constantes de niveau
  module) ni un changement dans le corps d'une classe hors méthodes.
- Une sélection `file` rejoue des tests préexistants (PAT-39, PAT-41, PAT-45, PAT-72) : le verdict
  reste mécanique mais une régression préexistante hors périmètre ferait aussi refuser. Un test
  protégé qui dépend du réseau ou d'un autre service n'est pas géré (aucun ne l'est dans ce corpus).
- Les tests tournent avec l'interpréteur et les dépendances installés sur la machine, sans
  environnement isolé du candidat : l'isolement du candidat relève du lanceur de comparaison.
- Énoncés : les 13 PR éligibles portent l'énoncé du tracker capturé le 2026-10-05
  (`ac_source: tracker_issue`, cases décochées), fourni à `snapshot --ac-overrides`. Les 71 autres
  PR gardent la description de la PR (`pr_body_summary`) ; elles sont exclues et ne peuvent pas
  produire de bundle.
- Éligibilité : 84 PR mergées, 26 de 300 lignes ou moins, 13 éligibles (79 squashs sur `main`,
  5 sur une branche empilée). Le tirage donne 6 + 6 tâches et une file de remplacement d'une seule
  PR (PAT-105, #81) ; un second échec de vérification épuiserait le tirage. Il n'y a pas
  d'extension à 12 tâches tenues à l'écart du tamis : une extension demande un nouveau tirage dans
  une version 2.

## Corpus tiré (graine `pat-19-protocol-v1`, algorithme `sha256-rank-v1`)

Même graine, même algorithme et même classement que dans la première version du tirage (renommage des ensembles seulement). « Tests protégés » compte les fonctions sélectionnées, un fichier entier comptant pour un.

| Ensemble | Ticket | PR | Lignes | Tests protégés | SHA de base | SHA de tête |
| --- | --- | --- | --- | --- | --- | --- |
| comparaison | PAT-35 | #26 | 42 | 1 | `a3f4e7f511` | `3a96eadf0b` |
| comparaison | PAT-45 | #38 | 229 | 1 | `04fe0e9a01` | `eae7231a89` |
| comparaison | PAT-34 | #25 | 132 | 3 | `ebd77b9229` | `a3f4e7f511` |
| comparaison | PAT-44 | #42 | 98 | 4 | `6befb22bda` | `2c1239cf45` |
| comparaison | PAT-41 | #33 | 262 | 6 | `8b128c57ba` | `37606adcc4` |
| comparaison | PAT-48 | #37 | 124 | 2 | `6a2f5ac35f` | `04fe0e9a01` |
| tamis | PAT-39 | #30 | 203 | 1 | `7db48eb7d8` | `8b128c57ba` |
| tamis | PAT-101 | #83 | 220 | 7 | `f740bebe48` | `58d3df8d1a` |
| tamis | PAT-36 | #27 | 150 | 3 | `3a96eadf0b` | `9fb276be29` |
| tamis | PAT-33 | #24 | 182 | 5 | `e70d50dd0f` | `ebd77b9229` |
| tamis | PAT-72 | #48 | 292 | 1 | `1f8658e4e0` | `9603302cc1` |
| tamis | PAT-28 | #19 | 153 | 5 | `3e2c66b614` | `772bb0baa0` |

SHA complets, critères d'acceptation et nœuds de test protégés : voir le manifeste. File de
remplacement : PAT-105 (#81). Écartées à la vérification : aucune. Les clés du manifeste sont `comparison` et `screening`.

## Vérification du juge sur les solutions d'origine

Pour les 12 tâches (comparaison et tamis), le juge accepte le diff mergé et refuse la base sans lui
(`verification` du manifeste : verdicts et comptes). Rejeu :

```
python3 -m foundry.local_first_corpus verify --repo <clone complet> \
  --snapshot docs/qualification/pat-19-corpus-snapshot-v1.json --seed pat-19-protocol-v1 \
  --manual-exclusions docs/qualification/pat-19-corpus-manual-exclusions-v1.json \
  --workdir <dossier jetable> --out <manifeste>
```

Le rejeu a besoin de l'historique git complet (objets des SHA de base et de tête) ; les tests
de la suite par défaut ne l'utilisent pas.

## Statut documentaire (AGENTS.md R5)

Artefacts documentés : le protocole et le corpus (ce dossier), la surface CLI de
`foundry.local_first_corpus` (section Outil), les critères et la sélection des tests protégés.
Non documenté car non nécessaire : aucune constante publique, option de `foundry_cli.py`, clé de
configuration ni table de routage n'a changé. L'application mécanique de R5 reste celle de
FOUNDRY-123.
