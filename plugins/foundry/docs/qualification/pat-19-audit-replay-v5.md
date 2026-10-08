# PAT-19 — Deux défauts d'instrument de la v5 : rejeu de l'audit et test dépendant du home (PAT-128)

PAT-128. Résultats bruts : [`pat-19-audit-replay-v5.json`](pat-19-audit-replay-v5.json) (règles qui ont tourné) et
[`pat-19-audit-replay-v5-quoted.json`](pat-19-audit-replay-v5-quoted.json) (mesure de la nouvelle coordonnée), schéma
`foundry.local-first-audit-replay.v1`. Cadre : PAT-ADR-0015 (un protocole gelé n'est jamais modifié, une donnée absente n'est
jamais zéro), FOUNDRY-ADR-0010 (aucun appel cloud, aucun modèle), FOUNDRY-ADR-0019, AGENTS.md R6 (aucun `claude` lancé).
Ce document ne modifie ni les protocoles v1 à v5, ni leurs configurations, ni aucun résultat : les 2 drapeaux de la v5 restent
comptés tels que la règle gelée les a comptés, les deux tâches (PR 42 et PR 33) restent **indécidées** dans L pour la v5, aucun
verdict, aucun rapport n'est recalculé. Étiquettes : **[vérifié]** (lu ou rejoué), **[déduit]**, **[inconnu]**.

## 1. Cause des deux drapeaux de contamination (critère 1)

**Ce qui a été fait.** `replay-audit` (voir « Rejeu hors ligne » dans [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md)) a relu les
69 enregistrements de `pat-19-x5compare-1` (`sha256` des résultats dans le JSON, identique au fichier versé), le registre et les
**86 flux bruts** (restés hors dépôt), sans pilote, sans modèle, sans appel cloud ; il lit des fichiers et appelle `git` en lecture
seule. Le JSON ne contient aucun flux brut, aucun nom du home (chemins sous le home remplacés par `~/<hidden>`, chemins de la racine
de travail comme dans les pièces de la v5).

**Fidélité** [vérifié] : pour les **69 enregistrements**, les constats du bras que rejouent les règles de la v5 (révision 2 et
politique `journal_under_observed_barrier`, champ `arm_findings_replayed.same_as_recorded`) sont **exactement** la liste de chemins
enregistrée, y compris les deux explorations contaminées. (Le champ `fidelity` hérité du rejeu de la v4 compare la révision 1 :
il ne s'applique pas à une campagne v5 enregistrée en révision 2, d'où ses 19 `mismatch` ; ce n'est pas un écart de fidélité.)

**Hypothèse des résultats de la v5** (« la phrase `Path '…' not found` de l'outil `read` n'est pas reconnue ») : **confirmée**.

- Dans les flux de PR 42 et PR 33 [vérifié], l'outil `grep` répond `Path not found: <chemin>` et l'outil `read` répond
  `Path '<chemin>' not found`, en erreur d'outil, avec exactement le chemin que l'appel avait donné. L'audit
  (`_missing_paths`) ne reconnaît que la première phrase (`_NOT_FOUND`) ; il range donc les chemins de `grep` dans `audit.not_found`
  et relève le chemin du `read` (et son écho dans le résultat : `tool_result:`), sous la racine de travail, sensible.
- Les chemins relevés sont ceux des deux appels `read` en erreur [vérifié] : `…/bundle/plugins/foundry/tooling/foundry/registry.py`
  (PR 42) et `…/bundle/plugins/foundry/tooling/foundry/frame.py` (PR 33, dossier de tentative au nom déformé). Aucun autre constat
  dans ces deux enregistrements.
- Contre-épreuve [vérifié] : avec la forme citée reconnue (section 2), le rejeu retire ces deux drapeaux et les consigne dans
  `not_found` ; **aucun autre constat** n'apparaît ou ne disparaît dans les 69 enregistrements (les constats décisifs de tous les
  flux cloud sont identiques) ; quatre autres explorations locales (PR 38, 30, 24, 48) gagnent des notes `not_found` pour la même
  phrase, sans effet sur leurs constats (déjà vides).
- Le fait que le modèle ait tapé un chemin déformé reste un comportement de l'explorateur [déduit des flux] ; l'audit n'a pas à
  le juger : le chemin n'existe pas, rien n'a été lu [vérifié par le texte de l'erreur de l'outil ; l'absence de lecture réelle
  d'un fichier hors zone n'est pas observée autrement, **[inconnu]** si le système de fichiers de la machine avait un autre
  contenu à ce chemin].

| Enregistrement | Enregistré (v5) | Règles de la v5 rejouées | Avec la forme citée (mesure) |
| --- | --- | --- | --- |
| L PR 42, exploration | `contaminated` : `read registry.py` + écho | identique à l'enregistré | plus de lecture ; 2 chemins inexistants à part (`docs`, `registry.py`) |
| L PR 33, exploration | `contaminated` : `read frame.py` + écho | identique à l'enregistré | plus de lecture ; 1 chemin inexistant à part |
| 67 autres | tels qu'enregistrés | identiques | identiques (hors 4 notes `not_found` ajoutées) |

Rien n'est corrigé après coup : ces deux enregistrements restent `contaminated` et leurs tâches indécidées pour la v5.
**Effet qu'aurait eu la forme citée sur le verdict de la v5 : [inconnu]** (la v5 n'est pas rejouée ; D aurait compté 12 tâches au lieu de 10
seulement si les deux explorations avaient aussi abouti à un rapport utilisable, ce que personne n'a lu).

## 2. Nouvelle coordonnée (critère 2)

`isolation.audit_absent_path_forms: "quoted_path"` (constantes `ABSENT_FORMS_KEY` et `ABSENT_FORMS`, `local_first_runner.py`).

- Effet : en plus de `Path not found: <chemin>`, l'audit reconnaît comme chemin absent la réponse d'erreur **entière** de l'appel
  `Path '<chemin>' not found`, où `<chemin>` est exactement le chemin que l'appel (omp) avait donné. Il est consigné à part
  (`audit.not_found`), n'est ni un accès ni un constat, et son écho dans le résultat n'en est pas un non plus. Tout le reste est
  inchangé : un autre texte d'échec, une réponse qui nomme un autre chemin ou ajoute du texte, une réponse qui n'est pas une erreur,
  la réponse d'un autre appel, une commande qui nomme le chemin, un second accès réussi au même chemin restent audités.
- Acceptée **seulement** sous un protocole postérieur à la v5 (`pat-19-protocol-v6` ou plus : ni v1 à v4, qui n'acceptent pas la
  révision 2, ni la v5, ni son pilote, dont le chargeur pose les coordonnées sans elle), avec `isolation.audit_revision` 2 ; une
  autre valeur est refusée. Un enregistrement sous la clé porte `audit.absent_path_forms` ; sans la clé, le champ est absent et
  tout enregistrement garde sa forme.
- Ce qui l'épingle : tests de `tests/test_local_first_audit_revision.py` (refus sous v1 à v5 et pilote, valeur unique, exigence de la
  révision 2, forme exacte, absence d'effet sans la clé, `FROZEN_PROTOCOLS` inchangé) ; les configurations v1 à v5 sont inchangées et
  continuent d'être épinglées par leurs empreintes.
- **Protocoles v1 à v5 inchangés** [vérifié] : sans la clé, `audit_transcript` (paramètre `absent_forms` à `False`) donne la même
  liste ; le rejeu des 69 enregistrements de la v5 **sans** la mesure est **identique octet pour octet** à celui d'avant la modification
  (hors le champ ajouté `arm_findings_replayed`, ajouté seulement aux rejeux de campagne à politique) ; le rejeu des 32 enregistrements de la
  v4 rend un résultat identique à [`pat-19-audit-replay-v4.json`](pat-19-audit-replay-v4.json) (enregistrements et résumé
  égaux) ; avec la mesure, les 32 classes de la v4 sont inchangées (4 notes `not_found` ajoutées sur des explorations locales).
  Pour mesurer la coordonnée sur une campagne qui ne la porte pas, `replay-audit --quoted-not-found` ; le résultat le dit
  (`absent_path_forms`), le défaut ne change pas.

## 3. Le test qui touche `~/.config/foundry/config.env` (critère 3)

**Identification** [vérifié]. Les traces `PermissionError: [Errno 1] Operation not permitted: '~/.config/foundry/config.env'` viennent de
`pathlib.Path.stat` (via `Path.exists()` de `config._load_dev_files` / `_dev_files`), exécuté par `doctor.main` dans trois tests de
`tests/test_doctor.py` (`test_doctor_never_emits_override_values_in_payload_or_output`,
`test_doctor_keeps_invalid_routing_and_unavailable_gate_visible_when_tracker_fails`,
`test_doctor_monorepo_fix_command_targets_existing_directory`), plus la famille `tests/test_local_scout.py` (« trusted secret configuration is invalid ») et, pour des chemins voisins du magasin d'état, deux tests de
`tests/test_agent_routing.py` (`test_local_fallback_claude_plan_*`). Le cadre `top = _toplevel(repository, runner)` des traces est du code
que le bras venait d'écrire dans son bundle (il n'existe pas dans le dépôt).

**Cause** [vérifié] : ces bundles sont des commits antérieurs à PAT-104 (#77), qui a introduit la fixture automatique
`_isolate_foundry_state` de `tests/conftest.py`. Sur les 12 tâches de la v5, 11 ont pour base un commit **sans** cette fixture (PR 19,
24, 25, 26, 27, 30, 33, 37, 38, 42, 48) ; la 12e (PR 83) l'a. Les flux qui portent l'erreur (sessions de 7 couples bras/tâche) sont
uniquement ceux de tâches antérieures à PAT-104 (PR 24, 26, 27, 37, 38, 42). Sur la base de PR 42, avec le vrai home et une sonde
d'`os.stat` (hors dépôt), deux des trois tests de `test_doctor.py` ci-dessus (le troisième échoue dans les flux mais n'a pas été pris par la sonde) lisent bien `~/.config/foundry/config.env`,
`~/.config/orfeo-poc/youtrack.env` et `~/.config/foundry/registry.json`, et un test de `test_agent_routing.py` un fichier d'escalade
sous `~/.config/foundry/` ; ils passent sans bac à sable (le fichier est lisible) et échouent sous le bac à sable natif, qui refuse le
`stat` (EPERM). C'est donc un test **du dépôt d'alors** qui dépendait de la configuration réelle du mainteneur.

## 4. Test et fixture : état du dépôt et correctif (critère 3, suite)

**Dépôt actuel** [vérifié] : la fixture automatique `_isolate_foundry_state` (PAT-104) redirige `HOME` vers un dossier temporaire par test et
efface `FOUNDRY_DATA`, `FOUNDRY_CONFIG` et `FOUNDRY_EXECUTION_RECEIPTS_DIR` ; un crochet d'audit refuse `open`, `listdir`, `mkdir`, etc.
sur le vrai `~/.config/foundry`. Une sonde d'`os.stat`/`os.lstat` (hors dépôt, sur la suite entière du CI, en-processus) ne voit
**aucun accès** au vrai `~/.config` hors le test qui pointe volontairement un résolveur vers lui
(`test_guard_rejects_a_resolver_pointing_at_the_real_home`, qui n'ouvre rien). **Il n'y a donc plus de test du dépôt actuel qui
dépende de la configuration réelle du mainteneur pour une lecture ou une écriture.** Hors périmètre de la sonde [inconnu] : les
sous-processus lancés par les tests.

**Le trou restant** [vérifié] : le crochet d'audit de Python n'a pas d'évènement `stat` ; un test, ou le code qu'il exécute, qui
ne fait que *regarder* le vrai fichier (`Path.exists()`) réussissait sans bruit sur la machine du mainteneur et n'échouait que sous
un bac à sable qui refuse ce `stat`. `tests/conftest.py` enveloppe désormais `os.stat` et `os.lstat` (ce qu'appellent `pathlib` et
`os.path.exists`) : tant que la garde est active, un chemin du vrai état Foundry (`~/.config/foundry`, `~/.config/orfeo-poc`) lève la
même erreur « PAT-104 guard ». `assert_state_resolvers_isolated` teste `_is_real_state_path` avant `resolve()` (qui fait un `lstat`).
Limites connues de la garde : `os.access`, `os.scandir` / `DirEntry.stat`, un chemin relatif donné avec `dir_fd` et les sous-processus
ne sont pas couverts. Les enveloppes sont aussi membres des ensembles `os.supports_*` de l'original (sinon `shutil.copystat` /
`copy2` sur des liens symboliques échouent), et la garde ne lève jamais d'elle-même (cwd supprimé : chemin non jugé).
Tests : `tests/test_state_isolation.py` (`stat`, `lstat`, `exists`, `is_file` refusés ; un chemin ordinaire non touché ; les fichiers de
configuration de développement se cherchent sous le home de test seulement).

**Reproduction sans bac à sable** [vérifié] : (a) sur l'archive du commit de base de PR 42 (`plugins/foundry` seul, hors dépôt), en
rendant le vrai `~/.config` illisible par une extension de test qui lève `EPERM` (sans rien changer au home réel) : **141 échecs**,
2276 réussites (127 de `test_local_scout.py`, plus `test_doctor.py`, `test_agent_routing.py`, et quelques tests de contrat
qui échouent aussi pour l'archive partielle, sans lien avec le home : le chiffre est donc un ordre de grandeur, non un décompte
exact de la seule cause) ; (b) observation distincte, sur le dépôt actuel et d'autres fichiers : `test_doctor.py`, `test_agent_routing.py`, `test_local_scout.py`,
`test_config.py` et `test_state_isolation.py`, avec la même extension, donnent **418 réussites et 1 échec**, qui est un artefact de
l'extension (elle lève `EPERM` avant la garde que `test_real_state_directory_is_unreachable` attend). Les deux nombres (a) et (b)
viennent de bases et de périmètres différents (suite entière de l'archive de PR 42, dont des échecs sans lien avec le home, contre cinq
fichiers du dépôt actuel) : ce ne sont **pas** un avant/après. Avec `HOME` pointé sur un dossier vide, la suite du CI donne le même
résultat qu'avec le home habituel.
Les « 156 échecs » cités par trois relecteurs de la v5 ne sont pas recomptés [inconnu] ; l'ordre de grandeur est cohérent avec (a).

