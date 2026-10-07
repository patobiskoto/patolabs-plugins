# PAT-19 — Résultats de la comparaison d'exploration, version 4

PAT-122. Cadre : PAT-ADR-0015 (exploration jugée seulement par son effet aval, règle écrite d'avance et jamais changée après
avoir vu les résultats, une donnée absente n'est jamais zéro, preuve insuffisante = conserver le cloud, aucune promotion),
FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance), FOUNDRY-ADR-0010 (enveloppe), FOUNDRY-ADR-0015 (coût lu dans les
journaux de session), FOUNDRY-ADR-0007 (aucun rôle local dans le produit). Protocole gelé :
[`pat-19-protocol-v4.md`](pat-19-protocol-v4.md) (règles héritées de la [v3](pat-19-protocol-v3.md) et de la
[v2](pat-19-protocol-v2.md)) ; configuration : `pat-19-campaign-v4.json` ; vérité terrain : `pat-19-exploration-truth-v2.json` ;
lanceur : [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) (section « Protocole v4 ») ; boucle opérateur :
[`pat-19-v4-operator.md`](pat-19-v4-operator.md) ; modèle de ce document : [`pat-19-exploration-results-v3.md`](pat-19-exploration-results-v3.md).
Aucune règle ni coordonnée du protocole v4 n'est modifiée par ce document ; il consigne un essai réel et s'arrête là.
Les nombres sont des décomptes recalculés sur les fichiers versés ; chaque lecture est marquée « lecture » ou « hypothèse »,
et ce qui vient d'un diagnostic indépendant ou d'une observation hors dépôt est dit tel.

## Résumé

Une campagne, `pat-19-x4compare-1`, le 2026-10-07 (heure locale, UTC+2) : comparaison A (Sonnet seul) contre L (Sonnet avec le
rapport de l'explorateur local qwen3.6-35b-a3b-mlx-4bit) sur les six tâches du tamis v3 (PR 30, 83, 27, 24, 48, 19), 6
lancements, 6 chargements du modèle (un avant chaque tâche ; 7 en comptant celui du lancement refusé), **30 exécutions cloud** (plafond 80), **7 004 823 tokens de
facturation premium** (A 4 134 697 ; L 2 870 126).

**Verdict tel que la règle gelée le rend** : décision `inconclusive`, `campaign_conclusion`
`keep_cloud_insufficient_evidence` ; pour L, compatibilité `pass`, qualité `unavailable`, économie `unavailable`. Par
PAT-ADR-0015, des preuves insuffisantes conservent le cloud. Aucune promotion, aucun modèle ni profil local activé, aucun
gain de facture annoncé.

Tâches acceptées : **A 1 sur 6** (PR 27, revue PASS), **L 0 sur 6**. Tâches indécidées (au moins une tentative marquée
contaminée) : A 4, L 4 ; décidées non acceptées : A 1 (PR 19), L 2 (PR 24, PR 19 ; sur PR 24 le juge a accepté le tour 1 et c'est la revue qui a bloqué). 8 des 32 enregistrements
sont `contaminated`. Un diagnostic en lecture seule, mené par un agent indépendant (voir « Contamination »), conclut que
**5 de ces 8 drapeaux sont des erreurs de l'audit** et que **3 sont des drapeaux corrects**. La règle gelée compte les 8
tels quels ; le verdict ci-dessus n'est pas recalculé « sans les faux drapeaux ».

Ce que les données soutiennent : la comparaison n'a de nouveau pas pu mesurer l'effet aval de l'exploration. Une seule tâche
(PR 19) est décidée dans les deux bras, refusée dans les deux. Le protocole v4 énonçait lui-même (section 5) le risque que la
racine privée ne fasse que déplacer le problème de contamination : il s'est réalisé, et, selon le diagnostic, le changement de la v4 qui a rendu
la racine de travail sensible a transformé deux défauts antérieurs de l'audit en drapeaux (voir « Contamination »). Six
tâches non indépendantes, déjà vues par l'explorateur, ne sont pas une preuve générale.

## Conduite de l'essai

- Code : `main` à `4adeb2f` (PAT-121 fusionné), arbre de travail gelé hors dépôt ; harnais omp 18.6.1 et LM Studio 0.4.25+1
  comme pour la v3 (non revérifiés pour cette campagne : hérités de la v3, voir « Ce qui reste inconnu ») ; contexte demandé 65 536
  (`-c 65536`). **Contexte observé** par le script opérateur (`lms ps`, journaux versés) : **262 144 à chacun des 7 chargements**
  (6 de la campagne et 1 du premier lancement refusé), malgré `-c 65536` : le plancher est respecté, mais la variable n'est
  toujours pas contrôlée (protocole v4 §5). Empreinte de campagne `3bc88cf4…`, manifeste `8ac65091…` (identique à la v3),
  enveloppe `1d05c1bf…`.
- Enveloppe `pat-19-x4compare-1` : mode `compare_exploration`, **80 exécutions cloud, 40 000 000 tokens premium, 100 000 s**
  (`envelope.json`). Plafond d'exécutions non atteint (30), plafond de tokens non atteint (7 004 823). Le plafond d'exécutions
  n'est donc pas exercé par cet essai : on ne peut dire que ce qu'il aurait fait au plafond.
- **Sauvegarde avant le premier essai** (observation du coordinateur, hors dépôt, non vérifiable depuis le dépôt) : l'état
  Foundry du mainteneur (`~/.config/foundry`) a été copié hors dépôt (dossier horodaté 16:09:37) avant le lancement ;
  `FOUNDRY_DATA` n'était pas exporté. La sauvegarde n'est pas versée (elle contient des secrets).
- **Autorisation des chargements** : le mainteneur a donné, dans la conversation et avant le lancement, son accord explicite
  pour charger qwen3.6-35b pour toute la comparaison v4, soit un rechargement avant chacune des 6 tâches (réponse « Oui
  enchaine »). C'est une **autorisation en bloc**, dite comme telle : chaque chargement n'a pas été confirmé un par un ; la boucle
  opérateur charge avec `-y` (voir `pat-19-v4-operator.md`).
- **Premier lancement refusé (19:00 local)** : le script opérateur a déchargé puis chargé le modèle (« loaded in 6.77s », 17:00:45Z
  dans le journal), puis le lanceur a refusé au préflight : `dedicated_machine_process_over_2gib:OrbStack:2887MiB` (code de sortie
  2, `launcher exit code 2 no work_remains line`), puis le script a déchargé le modèle. **Aucune exécution cloud**, aucune tentative.
  Le refus est dans le registre de campagne versé (premier `preflight`, `ok` faux). Le modèle a donc été chargé avant le refus du
  préflight, le chargement précédant le lanceur dans la boucle opérateur.
- **OrbStack** : le mainteneur a ensuite autorisé, dans la conversation, de quitter OrbStack le temps de la campagne ; il a été
  quitté, la campagne relancée à 19:56 et terminée à 21:04 (observation du coordinateur : OrbStack relancé après coup, ses 9
  conteneurs revenus ; application ChatGPT non lancée). Vérifié dans le registre versé : les 6 préflights « de départ » et les 6
  préflights par tâche de la seconde session sont acceptés (12 acceptés, 1 refusé en tout), plus gros autre processus relevé
  Claude (environ 1 Gio), sous le seuil de 2 Gio.
- **Boucle** : journal opérateur de la seconde session : 6 lancements (`launch 1` à `launch 6`), chacun précédé de
  `lms unload --all` et d'un chargement épinglé, les cinq premiers finissant par `work_remains=yes`, le sixième par
  `work_remains=no` ; code de sortie final 0 ; durée de 19:56:07 à 21:04:22 (environ 68 minutes). Aucun arrêt par le plafond
  (code 3), aucun arrêt par le garde du registre (code 4) : **vérifié** dans le journal (une seule sortie non nulle, le code 2 du premier
  lancement) et dans les résultats (aucun enregistrement ne porte `foundry_state_changed`).
- **Registre réel vérifié inchangé à la fin** : observation du coordinateur, hors dépôt (je n'ai pas lu `~/.config/foundry`) :
  même sha256 avant et après (5 607 octets, date de modification du 4 octobre), pas de code 4. Le garde automatique du lanceur
  (empreinte autour de chaque exécution cloud) n'a rien relevé. L'isolation `FOUNDRY_DATA` par exécution cloud est celle de PAT-120.
- Bornes de l'explorateur local : 900 s et 60 étapes ; un lancement par tâche ; ordre des tâches de la configuration ; bras A
  avant l'exploration de L pour une même tâche (ordre non contrebalancé, limite héritée de la v3).
- Les règles de la comparaison, le candidat, le retour au correcteur (20 noms, 300 caractères par message, 200 par nom,
  2 corrections) et la racine privée sont ceux du protocole gelé ; **rien n'a été changé après avoir vu un résultat**.

## Comparaison (`pat-19-x4compare-1`)

### Issues par bras et par tâche

Recalculées à partir de `results-pat-19-x4compare-1.jsonl` (32 enregistrements `attempt` : 6 explorations locales du bras L,
14 tentatives cloud du bras A, 12 du bras L). « Refusé » = refusé par le juge (tests protégés cachés). « Indécidé » = au
moins une tentative de la tâche porte un drapeau de contamination, la tâche n'est pas jugée plus loin (règle gelée).

| PR | A | L |
| --- | --- | --- |
| 30 | indécidé : tour 0 refusé (161 / 2), correcteur du tour 1 contaminé | exploration refusée (coupée par la borne de 60 étapes), implémenteur du tour 0 contaminé |
| 83 | indécidé : implémenteur du tour 0 contaminé | tour 0 refusé (17 / 1) ; tour 1 : juge ACCEPTÉ (18 / 0), revue BLOCK, contaminé (drapeau du relecteur) |
| 27 | **accepté** au tour 1 (juge 3 / 0, revue PASS) après un refus (2 / 1) | tours 0 et 1 refusés (2 / 1), correcteur du tour 2 contaminé |
| 24 | tours 0 et 1 refusés ; tour 2 : juge ACCEPTÉ (10 / 0), revue BLOCK, contaminé (drapeau du relecteur) | tour 0 refusé ; tour 1 : juge ACCEPTÉ (10 / 0), revue BLOCK (`review_block`) ; tour 2 refusé (4 / 6) |
| 48 | tours 0 et 1 refusés (223 / 122), correcteur du tour 2 contaminé | exploration contaminée ; aucun enregistrement d'implémenteur |
| 19 | refusé aux 3 tours (7 / 2) | refusé aux 3 tours |

Tâches acceptées : **A 1 sur 6, L 0 sur 6**. Tâches indécidées : A 4 (PR 30, 83, 24, 48), L 4 (PR 30, 83, 27, 48). Décidées
non acceptées : A 1 (PR 19, refusée par le juge aux 3 tours), L 2 (PR 19, refusée par le juge aux 3 tours ; PR 24, acceptée par le juge au tour 1 puis bloquée par la revue). **Une seule tâche est décidée dans les deux bras : PR 19, refusée dans les
deux** (décompte recalculé). Tentatives acceptées par le juge : A 2 (PR 27 ; PR 24 tour 2, contaminée), L 2 (PR 83 tour 1,
contaminée ; PR 24 tour 1, revue BLOCK). Sur PR 48, l'exploration contaminée de L est suivie d'aucun enregistrement
d'implémenteur : lecture du code du rapport (`local_first_runner.py` vers la ligne 4062) : une exploration contaminée n'est pas
« en attente de son implémenteur » mais décidée ; la raison exacte pour laquelle l'implémenteur n'a pas tourné n'a pas été
retracée dans le code.

### Table par tentative (cloud)

Complète, issue des résultats versés. « Retour reçu » = compteurs `feedback` de l'enregistrement (tests cachés échoués montrés
au correcteur / total échoués) ; vide pour les tours 0 et pour le tour qui suit un BLOCK de la revue (le retour de revue est
inchangé de la v3). « Non jugé » : le juge n'a pas tourné parce que la tentative est contaminée. Aucun échec de collecte
enregistré (`collection_failure` faux partout).

| PR | Bras | Tour | Juge (passés / échoués) | Retour reçu (montrés / total) | Revue | Issue enregistrée | Drapeau |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | A | 0 | REFUSED (161 / 2) | — | — | `judge_refused` | non |
| 30 | A | 1 | non jugé | 2 / 2 | — | `contaminated` | oui (bras) |
| 30 | L | 0 | non jugé | — | — | `contaminated` | oui (bras) |
| 83 | A | 0 | non jugé | — | — | `contaminated` | oui (bras) |
| 83 | L | 0 | REFUSED (17 / 1) | — | — | `judge_refused` | non |
| 83 | L | 1 | ACCEPTED (18 / 0) | 1 / 1 | BLOCK | `contaminated` | oui (relecteur) |
| 27 | A | 0 | REFUSED (2 / 1) | — | — | `judge_refused` | non |
| 27 | A | 1 | ACCEPTED (3 / 0) | 1 / 1 | PASS | `accepted` | non |
| 27 | L | 0 | REFUSED (2 / 1) | — | — | `judge_refused` | non |
| 27 | L | 1 | REFUSED (2 / 1) | 1 / 1 | — | `judge_refused` | non |
| 27 | L | 2 | non jugé | 1 / 1 | — | `contaminated` | oui (bras) |
| 24 | A | 0 | REFUSED (1 / 9) | — | — | `judge_refused` | non |
| 24 | A | 1 | REFUSED (4 / 6) | 9 / 9 | — | `judge_refused` | non |
| 24 | A | 2 | ACCEPTED (10 / 0) | 6 / 6 | BLOCK | `contaminated` | oui (relecteur) |
| 24 | L | 0 | REFUSED (1 / 9) | — | — | `judge_refused` | non |
| 24 | L | 1 | ACCEPTED (10 / 0) | 9 / 9 | BLOCK | `review_block` | non |
| 24 | L | 2 | REFUSED (4 / 6) | — | — | `judge_refused` | non |
| 48 | A | 0 | REFUSED (223 / 122) | — | — | `judge_refused` | non |
| 48 | A | 1 | REFUSED (223 / 122) | 20 / 122 | — | `judge_refused` | non |
| 48 | A | 2 | non jugé | 20 / 122 | — | `contaminated` | oui (bras) |
| 19 | A | 0 | REFUSED (7 / 2) | — | — | `judge_refused` | non |
| 19 | A | 1 | REFUSED (7 / 2) | 2 / 2 | — | `judge_refused` | non |
| 19 | A | 2 | REFUSED (7 / 2) | 2 / 2 | — | `judge_refused` | non |
| 19 | L | 0 | REFUSED (5 / 4) | — | — | `judge_refused` | non |
| 19 | L | 1 | REFUSED (7 / 2) | 4 / 4 | — | `judge_refused` | non |
| 19 | L | 2 | REFUSED (7 / 2) | 2 / 2 | — | `judge_refused` | non |

### Ce que le retour de tests a changé

Fait recalculé : sur les 14 tentatives dont le tour suit un refus du juge, l'enregistrement porte les compteurs `feedback` ; 11
d'entre elles ont un juge de part et d'autre du tour (les 3 autres, PR 30 A, PR 27 L et PR 48 A au tour 2, sont contaminées donc
non jugées). Sur ces 11 tours, **6 ont changé les comptes de tests** et **5 non** :

| Tâche / bras | Comptes avant, après (passés / échoués) | Retour reçu |
| --- | --- | --- |
| PR 27 A | 2 / 1, puis 3 / 0 (accepté) | 1 / 1 |
| PR 24 A | 1 / 9, puis 4 / 6 | 9 / 9 |
| PR 24 A | 4 / 6, puis 10 / 0 (juge ACCEPTÉ, revue BLOCK, contaminé) | 6 / 6 |
| PR 83 L | 17 / 1, puis 18 / 0 (juge ACCEPTÉ, revue BLOCK, contaminé) | 1 / 1 |
| PR 24 L | 1 / 9, puis 10 / 0 (juge ACCEPTÉ, revue BLOCK) | 9 / 9 |
| PR 19 L | 5 / 4, puis 7 / 2 | 4 / 4 |
| PR 48 A | 223 / 122, puis 223 / 122 | **20 / 122** (le cas de plus de 20 échecs) |
| PR 19 A | 7 / 2, puis 7 / 2, puis 7 / 2 | 2 / 2 chaque fois |
| PR 27 L | 2 / 1, puis 2 / 1 | 1 / 1 |
| PR 19 L | 7 / 2, puis 7 / 2 | 2 / 2 |

Un douzième tour comparable existe hors retour de tests, et il change aussi les comptes : PR 24 L, tour 2, qui suit le BLOCK de la revue (et non un refus du
juge), passe de 10 / 0 à 4 / 6 (régression). Sous la v3, aucun tour de correction n'avait changé un compte de tests sur les 32
refus de la comparaison ; sous la v4 sept des douze comparaisons de tours changent les comptes (six après retour de tests, une
après BLOCK). Un seul cas dépasse la borne de 20 noms (PR 48, 20 montrés sur 122).

Lecture (hypothèse, non établie) : l'instrument v3 pouvait bien ne pas montrer au correcteur ce qui échouait ; les données
v4 sont compatibles avec cette hypothèse (les comptes bougent) mais **ne la prouvent pas** : il n'y a pas de bras sans retour
joué sous la v4, et le protocole (§3.1 et §5) dit déjà que le message peut contenir l'expression d'une assertion et les valeurs
attendues, donc qu'un compte qui passe de 1 / 9 à 10 / 0 ne distingue pas une meilleure correction d'un ajustement au message.
Les tâches, le candidat, le nombre de bras et les exécutions ayant aussi changé, la v4 n'est pas comparable à la v3 tâche à
tâche (protocole §5). PR 48 (122 échecs invariants malgré 20 noms montrés) et PR 19 A (7 / 2 aux trois tours) ne bougent pas ; on ne sait pas
pourquoi.

## Contamination

Le rapport liste **8** tentatives contaminées sur 32 enregistrements : A PR 30 (tour 1), PR 83 (tour 0), PR 24 (tour 2), PR 48
(tour 2) ; L PR 30 (tour 0), PR 83 (tour 1), PR 27 (tour 2), et l'exploration locale de L sur PR 48. Aucune commande interdite
(`commands` vide pour les 8). Elles sont **appliquées telles que gelées** ; rien n'est reclassé ni rejoué. Aucun chemin du
répertoire personnel n'apparaît dans les enregistrements versés (la seule valeur hors racine de travail est `/`, voir le cas 1).

### Diagnostic indépendant (lecture seule)

Après la campagne, un agent indépendant a **rejoué `audit_transcript` en mémoire sur chaque flux brut** et **reproduit exactement
les 8 listes de chemins** des enregistrements, puis lu les commandes en cause. Ses constats, tels que transmis (les flux bruts
ne sont pas versés : ce document ne peut pas les faire vérifier depuis le dépôt) :

| # | Tentative | Ce qui s'est passé | Jugement du diagnostic |
| --- | --- | --- | --- |
| 1 | A PR 30, correcteur (tour 1) | `find / -iname "*ADR-0012*"` : parcours de tout le système de fichiers ; **10 noms de fichiers** du dossier personnel du mainteneur (dépôts sans rapport) sont arrivés au bras ; aucun contenu lu | drapeau **correct** |
| 2 | L PR 30, implémenteur (tour 0) | `find <racine de travail> -iname "*adr*0012*"` : sortie de la zone permise, aucun résultat | drapeau **correct** |
| 3 | A PR 83, implémenteur (tour 0) ; L PR 27, correcteur (tour 2) | `../../../tests/…` depuis un sous-dossier du bundle : un fichier du propre bundle du bras | **erreur de l'audit** |
| 4 | L PR 83 (tour 1) ; A PR 24 (tour 2) | le drapeau vient de la session du **relecteur** (qui lit son propre `scratch/`) ; la session du bras est propre | **erreur de l'audit** |
| 5 | A PR 48, correcteur (tour 2) | `cd ../..; … ls ..; ls ../scratch` avec un dossier de travail hérité d'un appel Bash précédent : son propre dossier de tentative ; le bras cherchait le fichier de test caché, à l'intérieur de la zone permise | **erreur de l'audit** |
| 6 | L PR 48, exploration locale | le modèle local a tapé un chemin faux d'un caractère (`/` au lieu de `-` dans le nom de la racine privée), inexistant ; « Path not found » ; il a recommencé avec le bon chemin | drapeau formellement **correct, sans accès** ; l'exploration a été comptée 0 pour une faute de frappe |

Vérifié par moi dans les fichiers versés ou le code (au-delà du diagnostic) : le texte `unknown.contaminated` des deux
enregistrements L PR 83 (tour 1) et A PR 24 (tour 2) dit que ce sont les appels du **relecteur** qui ont touché les chemins ;
la valeur `/` de A PR 30 (tour 1) ; le chemin enregistré pour l'exploration de L PR 48 contient `attempt-l06-0004/pr48-L-explore-…`
là où le registre nomme le dossier `attempt-l06-0004-pr48-L-explore-…` (un caractère de différence, comme dit) ; et la présence,
dans les flux bruts locaux, des commandes `find / -iname "*ADR-0012*"` (session de A PR 30 tour 1) et
`find <racine de travail> -iname "*adr*0012*"` (session de L PR 30 tour 0). **Non vérifié par moi** : le reste du diagnostic
(rejeu de l'audit, classement des cas 3, 4, 5 et 6, contenu de ce que `find /` a renvoyé, absence d'accès à d'autres contenus).

**Causes dans l'instrument** (diagnostic, lignes relues par moi dans `local_first_runner.py` à `4adeb2f`) :

- **(A)** la passe des jetons bruts résout chaque jeton relatif contre la racine du bundle, sans tenir compte d'un `cd` (ligne 1842 :
  `bundle / token`) : avec l'agencement v4, `bundle/../../..` est exactement la racine de travail ;
- **(B)** `_command_paths` repart du bundle à chaque appel Bash (ligne 1643 : `cwd, out = bundle, []`), alors que Claude Code
  conserve le dossier de travail d'un appel au suivant ;
- **(déclencheur)** la v4 a rendu la racine de travail sensible (ligne 2172 : `sensitive.append(self.work_root)`) : les deux
  défauts existaient avant ; selon le diagnostic, les chemins mal résolus ne tombaient alors sous aucune racine sensible (lecture
  non vérifiée pour tous les cas : pour les chemins issus de `_command_paths`, l'audit relève aussi les ancêtres du bundle par un
  autre chemin de code, lignes 1823-1825, ce qui n'a pas été rejoué sur la v3) ;
- **(imputation)** une contamination de la session du relecteur marque toute la tentative (les deux cas du 4).

Dit sans détour : le risque résiduel que le protocole v4 énonçait en section 5 (un geste `../../..` depuis un sous-dossier atteint
encore la racine de travail, devenue sensible, et est encore relevé) **s'est réalisé**, et, selon le diagnostic, le changement de la v4 qui rend la racine de travail sensible (protocole
§3.2, une mesure sur l'instrument et non une valeur de la règle) est ce qui a transformé deux défauts d'audit préexistants en
drapeaux. **5 des 8 drapeaux sont des erreurs de l'audit** (cas 3, 4, 5) selon le diagnostic ; la règle gelée les
compte quand même, et le verdict reste celui qu'elle donne.

**Aucun contenu d'une autre tentative, des tests cachés ou du fichier de vérité n'est arrivé à un bras** (constat du diagnostic ;
limites : la racine de travail est vide maintenant, et l'emplacement des tests cachés sur disque pendant le `find /` n'est pas
établi). **Recherche des tests cachés après retour** (diagnostic) : 2 des 15 sessions de correction sont sorties du bundle (cas 1
et 5) ; environ 10 sur 15 ont cherché le nom du test caché dans leurs propres tests visibles (décompte heuristique) ; aucune n'a
trouvé ni lu un fichier de test caché. Le protocole disait que le module et le nom du test sont visibles : ce
comportement n'est donc pas, en soi, une fuite, mais il montre que le retour de tests invite à chercher.

Ce que les enregistrements montrent pour les tentatives des cas 4 (**hors règle, non décisionnel, aucun verdict recalculé**) :
pour L PR 83 (tour 1) et A PR 24 (tour 2), le juge avait rendu ACCEPTÉ (18 / 0 et 10 / 0) et la revue BLOCK ; sans le drapeau,
l'issue enregistrée serait donc `review_block`, comme celle de L PR 24 (tour 1), non contaminée. Pour les cinq autres tentatives
contaminées de cloud (A PR 30 tour 1, L PR 30 tour 0, A PR 83 tour 0, L PR 27 tour 2, A PR 48 tour 2), aucun verdict de juge n'existe :
leur issue est INCONNUE.

## Blocages de la revue (juge ACCEPTÉ, relecteur BLOCK)

Trois tentatives : L PR 83 (tour 1), A PR 24 (tour 2), L PR 24 (tour 1). Les enregistrements ne portent que le verdict `BLOCK`, pas
sa raison. Selon le diagnostic (lecture des flux, non versés) : les raisons de blocage sont des tests ou une mise à jour de
documentation manquants (AGENTS.md R5) et, pour L PR 24, un changement hors périmètre ; **dans les trois, un dossier
`.pytest_cache/` commité fait l'essentiel du diff**. Qui, du bras ou de la capture de correctif de l'instrument, est responsable
de la présence de `.pytest_cache/` dans le diff **n'est pas établi**. Le seul enregistrement où le BLOCK s'applique sans autre
drapeau est L PR 24 (tour 1) ; le tour suivant (tour 2), qui suit ce BLOCK, régresse à 4 / 6 (voir plus haut).

## Qualité de l'exploration sur les six tâches

Notée par le même juge de localisation ; **informatif**, la règle de décision ne lit pas ces valeurs. Un refus ou une
contamination compte 0 (hypothèse héritée du filtre, non « donnée absente = zéro » pour la comparaison). Recalculé depuis
`exploration.score` et `local` de chaque enregistrement d'exploration.

| PR | Verdict | Rappel fonctions | Précision fichiers | Rappel fichiers | Secondes (étapes) | Tamis v3 (rappel fonctions) |
| --- | --- | --- | --- | --- | --- | --- |
| 30 | REFUSÉ (coupée à 61 étapes, borne de 60) | 0,0 | 0,0 | 0,0 | 291 (61) | 1,0 |
| 83 | scoré | 1,0 | 1,0 | 1,0 | 185 (35) | 0,333 |
| 27 | scoré | 1,0 | 0,75 | 1,0 | 148 (26) | 1,0 |
| 24 | scoré | 0,0 | 1,0 | 1,0 | 143 (33) | 0,0 |
| 48 | CONTAMINÉ (et le message final n'a pas fourni de rapport valide : « the report needs the keys files, functions, rationale ») | 0,0 | 0,0 | 0,0 | 283 (40) | 1,0 |
| 19 | scoré | 0,5 | 1,0 | 1,0 | 147 (27) | 0,5 |
| Moyenne sur 6 (refus et contamination à 0) | | **0,417** | 0,625 | 0,667 | 143 à 291 | 0,639 |

Moyenne sur les 4 explorations scorées seulement : rappel de fonctions 0,625, précision de fichiers 0,9375, rappel de fichiers
1,0 (calcul séparé, non la règle du filtre). Le rapport mécanique n'imprime pas ces moyennes. Lecture (hypothèse, non testée) :
sur les mêmes six tâches et le même candidat, le rappel de fonctions par tâche diffère du tamis v3 sur trois tâches (PR 30 et
PR 48 non scorées, PR 83 à 1,0 au lieu de 0,333) et coïncide sur les trois autres : la variabilité d'un essai à l'autre existe, on
n'en connaît pas la cause. Le candidat et le budget ont été retenus sur le score de ces tâches (protocole §5) : ces chiffres ne
mesurent pas une généralisation. Sur PR 48, l'enregistrement porte aussi une raison de refus (le message final ne contient pas de rapport valide) : sans le
drapeau, l'exploration aurait probablement été refusée pour ce motif (lecture) ; le verdict enregistré reste `CONTAMINATED`, et le
diagnostic dit que l'exploration a été comptée 0 pour une faute de frappe, ce qui n'est donc pas toute l'histoire.

Compatibilité (mécanique) : `pass` ; supplément de swap par exploration au maximum 0 MiB (le swap a baissé de 8 et de 16 MiB sur
PR 30 et PR 83), `ended_by_external_signal` faux sur les six (le `signal` 9 de PR 30 est l'arrêt de l'exploration par le lanceur
à la borne d'étapes). Jetons du flux local : 7 899 650 en entrée, 58 576 en sortie, 222 étapes au total (zéro token premium) ;
débits de génération et de préremplissage non exposés par le flux : inconnus.

## Coût

Définition (rapport) : somme non pondérée des quatre classes de tokens de facturation, tous modèles ; les lectures de cache
comptent comme n'importe quel token.

| Bras | Exécutions cloud | Tokens premium | Détail |
| --- | --- | --- | --- |
| A | 16 | 4 134 697 | 14 tentatives (PR 27 tour 1 et PR 24 tour 2 : 2 exécutions chacun, implémenteur plus relecteur) |
| L | 14 | 2 870 126 | 12 tentatives (PR 83 tour 1 et PR 24 tour 1 : 2 exécutions) ; 0 pour les 6 explorations locales (1 196 s) |
| Total | **30** | **7 004 823** | plafond 80 exécutions : non atteint |

Par tâche (exécutions ; tokens) : PR 30 A 2 ; 445 594, L 1 ; 200 500 — PR 83 A 1 ; 711 774, L 3 ; 917 963 — PR 27 A 3 ; 609 620,
L 3 ; 373 721 — PR 24 A 4 ; 698 844, L 4 ; 702 548 — PR 48 A 3 ; 860 065, L 0 — PR 19 A 3 ; 808 800, L 3 ; 675 394. Temps :
A 1 529 s, L 2 462 s (dont 1 196 s d'exploration locale).

Économie (rapport) : **`unavailable`**. A a une tâche acceptée, soit 4 134 697 tokens par tâche acceptée (tout le coût de A,
y compris les 5 tâches non acceptées, rapporté à la seule tâche acceptée) ; L n'en a aucune, donc **aucun ratio** (une donnée
absente n'est pas zéro) : le chiffre 2 870 126 < 4 134 697 ne dit rien d'une économie, L n'ayant fait accepter aucune tâche.
**Aucun gain de facture n'est annoncé.**

## Défaut du rapport mécanique

Le rapport imprime `informative_arms: ["E"]` alors que la v4 n'a pas de bras E. Le code (`local_first_runner.py`, vers la ligne
4044) écrit cette liste en dur, sans lire les bras joués. **Défaut de sortie du rapport**, non expliqué comme intentionnel ; la
décision et la conclusion ne le lisent pas (le bras L est bien `informative: false` dans le même rapport). Non corrigé ici (aucun
changement du lanceur dans cette PR). De même, `exploration_screening` est `no_screening` (candidat fixé), comme documenté.

## Les trois verdicts (PAT-ADR-0015), tels que la règle gelée les rend

- **Compatibilité** (bras L) : **pass**.
- **Qualité** (bras L) : **indisponible** (tâches indécidées dans les deux bras).
- **Économie** (bras L) : **indisponible** (aucune tâche acceptée dans L, pas de ratio par tâche acceptée).
- Décision `inconclusive`, `campaign_conclusion` `keep_cloud_insufficient_evidence` : le cloud est conservé. Aucune promotion,
  aucun modèle ou profil local activé, aucun défaut changé.

## Ce qui reste inconnu

- Ce que le juge aurait rendu sur les 5 tentatives cloud contaminées et non jugées (liste ci-dessus), et la suite des tours que
  les tâches indécidées auraient connus.
- Si le retour de tests est la cause des changements de comptes, ou s'il aurait suffi d'un tour de plus ; pas de bras témoin.
- Si les erreurs de l'audit auraient disparu avec une version corrigée de l'audit : non testé.
- La fréquence, à l'avenir, d'un chemin absolu d'une exécution antérieure laissé dans un correctif (cas symétrique du protocole §5).
- Pourquoi les comptes de PR 19 (7 / 2) et de PR 48 (223 / 122) ne bougent pas ; pourquoi `.pytest_cache/` figure dans le diff.
- La part du contexte observé (262 144 au lieu de 65 536) dans la variabilité de l'exploration.
- Les versions exactes des outils (omp, LM Studio) pour cette campagne : héritées de la v3, non relevées dans les journaux versés.
- Hors dépôt, non vérifiables d'ici : l'existence et le contenu de la sauvegarde, l'identité du sha256 du registre réel avant et
  après, l'état d'OrbStack et de ChatGPT, les accords donnés dans la conversation.

## Écarts et limites

- **Chargements de modèles** autorisés en bloc, non confirmés un par un ; **OrbStack** quitté sur autorisation du mainteneur ;
  **premier lancement refusé** puis relancé (conforme : un arrêt par le préflight est un résultat conforme).
- Les limites du protocole v4 (section 5) valent telles quelles : six tâches non indépendantes (cinq sur `trackers/linear.py`,
  quatre PR d'une même branche empilée), tâches déjà vues par l'explorateur local, résultats probablement favorables à L, retour
  de tests pouvant porter des valeurs attendues, contexte observé, v4 non comparable à la v3 tâche à tâche, pas de bras Haiku. À ces
  limites s'ajoutent celles de ce résultat : les trois défauts d'audit que nomme le diagnostic ((A), (B) et l'imputation au
  relecteur), et un seul essai par couple tâche / bras.
- Six tâches ne sont **pas** une preuve générale ; une seule machine, un seul moteur local (MLX), un seul candidat ; ce résultat
  ne dit rien d'autres familles de tâches ni d'un explorateur cloud économique.
- **Incident de confinement, distinct de l'audit** : le `find /` du cas 1 a fait parvenir à une session cloud 10 noms de fichiers
  du dossier personnel du mainteneur (aucun contenu lu, selon le diagnostic). L'audit l'a relevé après coup, il ne l'a pas
  empêché : les bras cloud tournent sans bac à sable (AGENTS.md R6), risque résiduel connu et accepté par le mainteneur, qui s'est
  réalisé ici. Le réduire (règle d'outil refusant un parcours hors du bundle, par exemple) demande un ticket propre passant par
  `foundry:intake`.
- **Suite possible (non engagée ici)** : réparer l'audit (jetons relatifs qui ignorent un `cd`, dossier de travail réinitialisé
  à chaque appel, imputation au relecteur) et le champ `informative_arms` demande un ticket propre passant par `foundry:intake` ;
  cette PR ne change aucun code du lanceur et ne relance rien.

## Pièces versionnées

Dans [`pat-19-runs/x4compare-1/`](pat-19-runs/x4compare-1/), copiées telles quelles : `ledger-pat-19-x4compare-1.jsonl`,
`results-pat-19-x4compare-1.jsonl`, `report-pat-19-x4compare-1.json`, `envelope.json`, les deux `operator-compare-*.log` (le
premier est celui du lancement refusé), `streams-manifest.json` (sha256 et taille des 36 flux : 30 sessions cloud et 6
explorations locales, environ 20 Mo). Contrôle avant versement : aucun secret, jeton, adresse, ni chemin de dossier personnel dans
ces fichiers ; **les noms de fichiers du dossier personnel du mainteneur (cas 1) n'apparaissent que dans les flux bruts, non
versés**, et le manifeste ne donne que des noms de session et d'exploration. Les flux d'événements bruts ne sont pas versionnés
(ils embarquent des contenus du dépôt et de la machine) ; ils restent sur la machine du mainteneur. Les journaux opérateur sont
copiés tels quels, avec les séquences d'affichage de progression du chargement (comme pour la v3). Tous les chiffres sont
recalculés à partir des fichiers versés, hors les observations hors dépôt et le diagnostic signalés dans le texte.

## Statut documentaire (R5)

Artefacts ajoutés : ce document et `pat-19-runs/x4compare-1/` ; un lien dans la section « Protocole v4 » de
`pat-19-launcher-v1.md` ; une entrée de CHANGELOG, dont la correction de la formulation de l'entrée PAT-121 sur le masquage
(seule la graphie résolue de la racine de travail est masquée, et ce masquage était une décision du coordinateur signalée au
mainteneur sans validation explicite). Aucun verbe ni option de `foundry_cli.py`, clé de configuration, constante publique ou
table de routage n'a changé ; protocole v4, configuration v4 et résultats v1 à v3 inchangés ; aucun code. Le détecteur de
FOUNDRY-123 n'est pas livré : ce statut est affirmé ici et vérifié en revue, non appliqué mécaniquement.
