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
| `pat-19-corpus-statements-v1.json` | Énoncés des 13 tickets éligibles, tels que capturés dans le tracker le 2026-10-05 (cases décochées), expurgés (voir « Expurgation ») : seule source du libellé `tracker_issue` |
| `pat-19-corpus-snapshot-v1.json` | Entrée figée : les 84 PR mergées (numéro, ticket, SHA de base, SHA fusionné, fichiers avec lignes ajoutées/supprimées, critères d'acceptation, tests protégés) ; reproductible depuis le dépôt (`gh pr list` + fichier d'énoncés, dont le sha256 et la date de capture sont consignés) |
| `pat-19-corpus-classification-overrides-v1.json` | Décisions manuelles `include` / `exclude`, chacune avec une raison obligatoire |
| `pat-19-corpus-manifest-v1.json` | Tirage (graine, algorithme, décisions manuelles appliquées, exclusions par PR), 6 tâches de comparaison + 6 de tamis nominatives avec leur rejouabilité, file de remplacement, preuve du juge, limites |
| `../../tooling/foundry/local_first_corpus.py` | Outil (aucune dépendance réseau au rejeu) |
| `../../tests/test_local_first_corpus.py` | Tests déterministes de l'outil et cohérence des fichiers ci-dessus |

## Outil

Depuis `plugins/foundry/tooling` (Python 3, `git` et `pytest` locaux uniquement) :

```
python3 -m foundry.local_first_corpus snapshot --repo <repo> --prs-json <gh.json> [--ref SHA] --statements docs/qualification/pat-19-corpus-statements-v1.json --out <snapshot>
python3 -m foundry.local_first_corpus draw   --snapshot <snapshot> --seed <graine> [--overrides <json>] [--out <json>]
python3 -m foundry.local_first_corpus bundle --repo <repo> --snapshot <snapshot> --pr <n> --dest <dossier>
python3 -m foundry.local_first_corpus judge  --repo <repo> --snapshot <snapshot> --pr <n> --candidate <bundle>
python3 -m foundry.local_first_corpus verify --repo <repo> --snapshot <snapshot> --seed <graine> [--overrides <json>] --workdir <dossier> --out <manifest>
```

- `snapshot` : `<gh.json>` est la sortie de `gh pr list --state merged --limit 200 --json
  number,title,body,mergeCommit,baseRefOid,headRefOid,baseRefName`. Le SHA de base est le
  parent du commit de squash ; le SHA de tête est le commit de squash fusionné (le SHA de tête de
  la branche de PR est conservé en `pr_head_sha`). Un parent unique ne suffit pas : le sujet du
  commit fusionné doit finir par `(#<n°>)` et celui de son parent non ; sinon la PR est
  `not_replayable` avec sa raison (`merge_commit_not_a_squash_of_this_pr`,
  `rebase_merge_of_several_commits`, `not_single_parent_squash_commit`). Cinq PR (PAT-33, 34, 35, 36, 38) ont été
  fusionnées sur la branche empilée de PAT-22 et non sur `main` : leur squash a un seul parent, elles
  sont rejouables (`merge_kind: squash_on_stacked_branch`).
- `draw` : classe les PR éligibles par `sha256("<graine>:<numéro de PR>")` croissant ; les 6
  premières forment l'ensemble de **comparaison**, les 6 suivantes le **tamis** local
  (protocole section 4), le reste est la file de remplacement ordonnée. Les deux ensembles sont
  disjoints : une tâche de comparaison n'a jamais servi au tamis (vérifié à la finalisation et testé). Rejeu : même snapshot + même graine = même liste (testé).
- `bundle` : l'arbre du SHA de base est exporté (`git archive`) sous `--dest`, les tests protégés
  en sont retirés (avec les commentaires contigus au-dessus d'une fonction retirée), puis un
  **nouveau** dépôt est initialisé avec un unique commit racine (auteur neutre, date fixe, sans
  remote, sans `alternates`, sans lien de worktree, sans reflog hérité). Le candidat dispose d'un
  dépôt pour `git diff`, mais ni le SHA fusionné, ni les tests protégés, ni le chemin du dépôt de
  développement n'y figurent, et un `commit`/`stash`/`branch`/`gc` n'écrit pas dans le dépôt de
  développement (testé). Aucun `git worktree` n'est utilisé. `--dest` doit ne pas exister et ne pas
  être dans le checkout de développement ; l'outil ne supprime jamais un dossier qu'il n'a pas créé
  dans cette exécution. `TASK.md` est l'énoncé du ticket (titre et corps) tel que dans
  `pat-19-corpus-statements-v1.json` (jamais le diff mergé, la description de la PR ni les tests
  protégés). `bundle` refuse (échec fermé) toute PR dont `ac_source` n'est pas `tracker_issue`.
- `judge` : voir ci-dessous ; code de sortie 0 si `ACCEPTED`, 1 sinon.
- `verify` : pour chaque tâche tirée, construit deux bundles jetables (supprimés ensuite) (base + diff mergé hors
  tests protégés ; base seule), lance le juge sur chacun, écarte la tâche si le juge n'accepte pas
  la solution d'origine ou accepte la base, la remplace par la file de remplacement (un créneau
  défaillant prend la première entrée restante), et consigne la raison. Une erreur d'outillage
  (`CorpusError` : commit introuvable, bundle impossible, diff qui ne s'applique pas) **interrompt**
  la vérification : ce n'est pas un verdict et elle ne déclenche jamais un remplacement par la file
  (PAT-108).

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
  `pat-19-corpus-classification-overrides-v1.json` (PAT-102, autorité de la dérogation humaine de
  clôture d'Epic).

Les mots-clés du titre sont comparés sans accents ni casse (normalisation Unicode) : « basculé »
et « basculer » sont équivalents. Une décision manuelle `include` (raison obligatoire, consignée
dans `draw.overrides` du manifeste) ne lève que les raisons `topic:*` ; elle ne lève jamais la
taille, le code et les tests, la rejouabilité ni l'énoncé du tracker. Une seule : PAT-44 (#42,
« …dépôt basculé… ») est une réparation après le cutover, pas une migration de données ; la règle de
titre l'attrapait par accident. Le tirage est inchangé (mêmes 12 PR, mêmes files).

Les règles de sujet sont volontairement étroites et lisibles, pas exhaustives : un cas limite se
traite dans le fichier de décisions manuelles, jamais par un choix non consigné.

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

`judge` prend un bundle candidat et, dans cet ordre :

0. refuse par une erreur (`CorpusError`, pas un verdict) un candidat situé dans le checkout de
   développement, qui n'est pas un bundle (pas de `.git`) ou qui a déjà été jugé : le juge écrit
   les tests protégés dans l'arbre, un bundle jugé ne repart jamais chez un candidat (un fichier
   `foundry-judged` dans son `.git` le consigne ; PAT-108) ;
1. refuse (`REFUSED`, raison dans `note`) s'il n'y a aucun test sélectionné (jamais la suite
   entière), si un chemin protégé (ou un de ses dossiers parents) est un lien symbolique, ou si le
   candidat a créé, modifié ou supprimé n'importe où dans le bundle un `conftest.py`, `pytest.ini`,
   `pyproject.toml`, `tox.ini`, `setup.cfg`, `sitecustomize.py`, `usercustomize.py` ou `*.pth`
   (comparaison avec l'arbre du SHA de base, équivalent du commit racine du bundle ; les chemins
   protégés sont exemptés puisqu'ils sont écrasés). Les dossiers d'environnement que le candidat a
   créés (`.venv`, `venv`, `node_modules`, ou tout dossier contenant un `pyvenv.cfg`) et qui
   n'existent pas dans l'arbre de base sont ignorés par ce contrôle, et ne sont jamais sur le
   chemin de l'interpréteur jugé (PAT-108) ;
2. écrase les chemins protégés avec les versions du SHA fusionné (supprime d'abord le fichier,
   n'écrit jamais à travers un lien) ; le fichier du candidat à ces chemins ne compte donc jamais ;
2b. supprime tout `*.pyc` et tout `__pycache__` de l'arbre du candidat (un `.pyc` précompilé,
   par exemple en hash non vérifié, remplacerait la source importée) ;
3. lance `python -P -m pytest` sur les seuls identifiants sélectionnés, avec `-c` **toujours**
   donné (sur le `pytest.ini` du SHA fusionné, ou à défaut sur un `[pytest]` vide du juge : ni
   `pytest.toml`, ni `.pytest.ini`, ni `[tool.pytest]` d'un candidat n'est donc lu), `--rootdir` sur `plugins/foundry`, `--confcutdir` sur `tests/` (un
   `conftest.py` au-dessus de `tests/` n'est pas chargé), `-p no:cacheprovider`, sans le répertoire
   courant dans `sys.path`, dans un environnement réduit à `PATH`, `LANG`, `LC_ALL`, plus `HOME` et
   `TMPDIR` pointant vers un dossier temporaire neuf, `PYTHONDONTWRITEBYTECODE`,
   `PYTHONPYCACHEPREFIX` (dossier temporaire du juge) et `PYTHONNOUSERSITE` (aucune variable `FOUNDRY_*`, aucun jeton ; testé), avec un délai de 900 s
   (`REFUSED`, `note: timeout`).

Il rend `ACCEPTED` si et seulement si pytest sort avec 0, au moins un test passe, et aucun test
n'échoue, n'est en erreur ou ignoré ; sinon `REFUSED`, avec les comptes. Le verdict liste
`tripwire` et `changed_outside_product` (fichiers modifiés par le candidat hors de l'arbre
produit : tests, fixtures, docs).

Ce que le juge **empêche** : un test du candidat à un chemin protégé ou dans un autre fichier
(jamais sélectionné), l'édition de la configuration pytest ou d'un `conftest.py`, les liens
symboliques sur les chemins protégés, le contournement par bytecode précompilé, la
réutilisation d'un bundle déjà jugé, l'usage du `HOME` et de l'environnement réels du
développeur, un `conftest.py` créé au-dessus de `tests/`.

Ce que le juge **n'empêche pas** : le code produit est importé dans le processus de test et peut
altérer pytest ou le verdict (par exemple en patchant `pytest` à l'import) ; les fichiers de support
non protégés (fixtures, données, assistants sous `tests/`) peuvent être modifiés par le candidat,
ils sont seulement listés dans `changed_outside_product` ; le candidat peut lire le disque et le
réseau pendant son propre essai ; les tests lancés ne sont pas isolés du reste de la machine
(interpréteur et dépendances installés). Le verdict est mécanique, pas une preuve d'honnêteté : un
bundle suspect se relit à partir de `git diff` contre le commit racine.

Limites connues :

- La sélection est faite au niveau fonction ; elle ne capte pas un changement de comportement d'un
  test causé par une autre modification de module (hors assistants et constantes de niveau
  module) ni un changement dans le corps d'une classe hors méthodes.
- Une sélection `file` rejoue des tests préexistants (PAT-39, PAT-41, PAT-45, PAT-72) : le verdict
  reste mécanique mais une régression préexistante hors périmètre ferait aussi refuser. Un test
  protégé qui dépend du réseau ou d'un autre service n'est pas géré (aucun ne l'est dans ce corpus).
- Les tests tournent avec l'interpréteur et les dépendances installés sur la machine : l'isolement
  du candidat pendant son essai relève du lanceur de comparaison.
- Énoncés : les 13 PR éligibles portent l'énoncé du tracker tel que capturé le **2026-10-05**
  (`ac_source: tracker_issue`, cases décochées, expurgé), pas tel qu'il était au SHA de base ;
  ils viennent uniquement de `pat-19-corpus-statements-v1.json`, dont le sha256 et la date sont
  consignés dans le snapshot et le manifeste. Les 71 autres PR gardent la description de la PR
  (`pr_body_summary`) ; elles sont exclues et ne peuvent pas produire de bundle.
- Éligibilité : 84 PR mergées, 26 de 300 lignes ou moins, 13 éligibles (79 squashs sur `main`,
  5 sur une branche empilée). Le tirage donne 6 + 6 tâches et une file de remplacement d'une seule
  PR (PAT-105, #81) ; un second échec de vérification épuiserait le tirage. Il n'y a pas
  d'extension à 12 tâches tenues à l'écart du tamis : une extension demande un nouveau tirage dans
  une version 2.
- **Indépendance des deux ensembles** : disjoints par ticket, pas indépendants. PAT-33 (tamis),
  PAT-34 et PAT-35 (comparaison) et PAT-36 (tamis) sont des commits consécutifs d'une même branche
  empilée ; PAT-35 et PAT-36 sont deux correctifs jumeaux de `import_adr_batch` ; PAT-39 (tamis)
  précède PAT-41 (comparaison) ; 8 des 12 tâches touchent `tests/test_linear_tracker.py`. Un
  résultat du tamis ne généralise donc pas librement à la comparaison. Le tirage n'est pas modifié ;
  la limite est consignée dans `limits` du manifeste.
- **Rejouabilité** : les commits de base des tâches empilées (#24 à #27) ne sont sur aucune branche
  de `main` ; ils n'existent que sur des branches locales du développeur et sur aucune référence
  `origin/*` connue localement. Le manifeste consigne, par tâche, `replayability` (SHA de base et de
  tête présents localement, nombre de références `origin/*` connues qui les contiennent
  (`origin_refs_containing_count` : un nombre et non les noms, propres à la machine, pour que le manifeste soit
  reproductible), accessibilité
  publique : `origin_refs` ou `unknown`). Pour les quatre tâches empilées, l'accessibilité publique
  est `unknown` (aucun réseau utilisé) ; à essayer : `git fetch origin
  feat/pat-22-livrer-le-stockage-adr-versionné` (seulement si la branche existe encore sur le
  dépôt public), sinon ces tâches ne se rejouent que sur le dépôt qui a produit le snapshot.

## Expurgation des énoncés (dépôt public)

Avant d'être commités et remis à un candidat, les énoncés sont expurgés
(`scrub_statement`, testé) : les liens Markdown vers `linear.app` sont remplacés par leur texte
(les identifiants de ticket restent), les URL `linear.app` nues sont supprimées, tout UUID (par
exemple un identifiant d'état natif) est remplacé par `<uuid removed>` ; le reste est conservé tel
quel. `load_statements` refuse un fichier mal nommé ou non expurgé. Les autres fichiers de données
du corpus sont testés sans URL `linear.app` ni UUID.

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
(`verification` du manifeste : verdicts et comptes), avec le bundle sans lien et le juge à
environnement réduit décrits plus haut (12 sur 12, comptes identiques à ceux d'avant le
durcissement). Rejeu :

```
python3 -m foundry.local_first_corpus verify --repo <clone complet> \
  --snapshot docs/qualification/pat-19-corpus-snapshot-v1.json --seed pat-19-protocol-v1 \
  --overrides docs/qualification/pat-19-corpus-classification-overrides-v1.json \
  --workdir <dossier jetable> --out <manifeste>
```

Le rejeu a besoin de l'historique git complet (objets des SHA de base et de tête) ; les tests
de la suite par défaut ne l'utilisent pas.

## Statut documentaire (AGENTS.md R5)

Artefacts documentés : le protocole et le corpus (ce dossier), la surface CLI de
`foundry.local_first_corpus` (section Outil : options `--statements` et `--overrides`), les critères
et la sélection des tests protégés, le fichier d'énoncés et la règle d'expurgation, ce que le juge
empêche ou non, les limites du corpus.
PAT-108 (suites des revues de PAT-107) : durcissements du juge et de `verify` décrits ci-dessus
(bytecode, bundle jugé une seule fois, checkout de développement refusé, dossiers d'environnement,
`-c` inconditionnel, erreur d'outillage qui interrompt), clé de rejouabilité
`origin_refs_containing_count`. Le manifeste est régénéré par `verify` (les 12 tâches sont
inchangées : 12 sur 12 `ACCEPTED` avec la solution mergée et `REFUSED` sur la base). Le lanceur est
décrit dans [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md).
Non documenté car non nécessaire : aucune constante publique, option de `foundry_cli.py`, clé de
configuration ni table de routage n'a changé. L'application mécanique de R5 reste celle de
FOUNDRY-123.
