# Décision de promotion Claude Haiku 5.5 (PAT-125)

Statut : **promotion non appliquée, en attente de la décision du mainteneur**. La
promotion a été demandée et cadrée par PAT-ADR-0016 (proposée, acceptée au merge).
L'unique essai natif a été exécuté le 9 octobre 2026 ; son verdict consigné est
`not_conforming` (voir « Résultat de l'essai »). Conformément à l'ADR, le titulaire
Haiku 4.5 sans effort reste le défaut `economy`. Seuls sont livrés la déclaration de
`haiku-5.5`, ses profils, la règle de version minimale et l'outil d'essai.
Cette note consigne la décision humaine. Elle ne crée aucune preuve ni receipt.

## Approbation et périmètre

Le 7 octobre 2026, le mainteneur a demandé la promotion de Claude Haiku 5.5. Le
8 octobre 2026, il a retenu : installer et promouvoir, effort `medium`, retour arrière,
ADR, merge après revue et CI. PAT-ADR-0016 applique le régime du candidat moins cher de
FOUNDRY-ADR-0019.

| Tier | Défaut en vigueur (PAT-16) | Défaut demandé, non appliqué | Rôle principal |
| --- | --- | --- | --- |
| economy | `haiku-4.5`, effort `null` non applicable | `haiku-5.5` (`claude-haiku-5-5`), effort `medium` | scout |

Le changement demandé porte sur le tier : s'il était appliqué, il vaudrait aussi pour
tout rôle non-gate qui retombe sur `economy` par le fallback descendant existant. Les tiers `balanced`, `frontier` et
`apex`, les planchers de tier et d'effort du reviewer et de l'architecte, les défauts
Codex, les rôles, permissions, plafonds de tours et règles d'escalade ne changent pas.
L'alias court `haiku` n'est pas promu. Haiku 4.5 reste un pin historique exact.

## Ce qui est affirmé et ce qui ne l'est pas

- Affirmé : le prix catalogue par token de Haiku 5.5 est inférieur à celui de Haiku 4.5
  aux deux paliers tarifaires (page de prix du fournisseur relevée le 8 octobre 2026,
  citée par PAT-ADR-0016). C'est la seule base du « moins cher ».
- Non affirmé : aucun gain comparatif mesuré, de qualité ou de coût ; aucune économie
  de facture ou de quota ; aucun coût par tâche. Aucun benchmark, rejeu, comparaison ou
  matrice d'efforts n'a été lancé. L'effort `medium` est le défaut du fournisseur et le
  choix du mainteneur, pas le résultat d'une mesure.
- Risque connu, non surveillé par la télémétrie Foundry : au-delà de 100 000 tokens de
  prompt le prix catalogue est multiplié par cinq, le contexte 1M admet des prompts plus
  longs et l'effort `medium` ajoute des tokens de raisonnement. Le coût par tâche peut
  augmenter ; un coût inconnu reste inconnu, jamais zéro.
- Les résultats de PAT-19 et les observations de PAT-16 restent liés à Haiku 4.5 et ne
  se transfèrent pas à Haiku 5.5.

## Version minimale de l'hôte

Le profil `haiku-5.5` exige Claude Code 2.1.293 ou supérieur. Sous cette version
observée, le lancement est refusé avec un message nommant la version requise, sans
repli vers Haiku 4.5, l'alias `haiku` ou un autre modèle. La manière d'observer la
version et la règle d'une version non observable (jamais présentée comme conforme) sont
décrites dans [`model-routing.md`](../model-routing.md), section PAT-125. Les lanceurs
sans interface (`command_runtime.py`, `campaign_runtime.py`) appliquent la même règle au
binaire qu'ils vont lancer, par son `--version`. Conséquence cassante si la promotion
était appliquée : un hôte antérieur à 2.1.293 perdrait le tier `economy` par défaut
jusqu'à sa mise à jour ou jusqu'à un mapping projet explicite vers Haiku 4.5.

## Essai natif de compatibilité

PAT-ADR-0016 autorise, avant le changement du défaut, UN essai borné sur la machine du
mainteneur : un parent, un enfant scout, profil `haiku-5.5` / `medium`, vingt minutes
au maximum, concurrence un, aucun rejeu, aucun crédit API ou supplémentaire. C'est une
fumée de compatibilité : ni benchmark, ni comparaison, ni fondement de la promotion.

Commande, depuis la racine du dépôt, lancée par le coordinateur (jamais par les tests) :

```bash
PYTHONPATH=plugins/foundry/tooling python3 -m foundry.claude_profile_trial \
  --work-dir "$HOME/pat-125-native-trial" \
  --out plugins/foundry/docs/qualification/pat-125-native-trial.json
```

`--dry-run` prépare la fixture dans le dossier donné et affiche la commande sans rien
lancer ; le lancement réel exige ensuite un autre dossier, car un dossier de travail ou
un fichier de résultat existant est refusé (aucun rejeu).

- Ce que fait l'outil : il crée hors de tout dépôt git une fixture portant sa propre
  politique (`economy` → `haiku-5.5` / `medium`, le défaut du produit n'est donc pas
  requis), vérifie hors ligne que ce checkout la résout vers
  `routed-readonly-medium-haiku-5.5`, puis lance un seul `claude -p` (parent `sonnet`,
  hors essai) avec `--plugin-dir` sur le `plugins/foundry` de CE checkout, le mode
  source des essais PAT-16. Le parent délègue une fois à `foundry:lupin`, qui lit un
  jeton dans la fixture. Le groupe de processus est tué à vingt minutes.
- Environnement de l'enfant : la liste R6 (`HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`,
  `TMPDIR`, `USER`) plus `FOUNDRY_RUNTIME_CONFIG_ISOLATED=1` et un `FOUNDRY_DATA` dans
  le dossier de travail. Aucune clé d'API ni variable de surcharge de l'hôte appelant
  n'est transmise ; l'identité OAuth de la machine est conservée.
- Checkout et non cache installé : le cache installé (1.0.0) n'a ni profil `haiku-5.5`
  ni traduction pour ce modèle et refuserait la politique de la fixture. Le type d'agent
  réellement lancé, lu dans les métadonnées natives de l'enfant, doit être
  `foundry:routed-readonly-medium-haiku-5.5`, profil qui n'existe que dans ce checkout ;
  le chemin du plugin de l'événement d'initialisation est relevé quand l'hôte le donne.
- Ce qu'il écrit : le flux brut et la sortie d'erreur restent dans le dossier de travail,
  hors dépôt. Le seul fichier de résultat est celui de `--out`
  (`foundry.pat125-native-trial.v1`) : bornes, référence du source, `requested`
  (politique), `transmitted` (profil, modèle et effort sélectionnés, calculés hors ligne
  avant le lancement), `hook` (ce que le crochet a déclaré, dont la version d'hôte qu'il
  a observée, quand l'hôte l'a gardé dans son journal), `observed` (types d'agent,
  modèles, `effort` et `perTurnEffort`, versions, lus dans le journal natif de
  l'enfant), `conformity`, `verdict` et `not_established`. Ni transcript, ni identifiant
  de session, ni chemin du dossier personnel.
- Résultat conforme : code de sortie 0 sans dépassement, exactement un enfant de type
  `foundry:routed-readonly-medium-haiku-5.5`, modèles observés `["claude-haiku-5-5"]`,
  efforts observés `["medium"]`, version d'hôte observée au moins 2.1.293, jeton rendu ;
  `verdict` vaut alors `conforming`. Une valeur divergente donne `not_conforming`. Une
  observation absente donne `unknown`, jamais conforme. Un identifiant daté
  (`claude-haiku-5-5-AAAAMMJJ`) est rapporté `dated_snapshot` avec le verdict `unknown` :
  PAT-ADR-0016 nomme `claude-haiku-5-5`, la décision revient au mainteneur.
- Si l'essai échoue ou n'est pas observé conforme, Haiku 4.5 reste le défaut et seule
  la déclaration est livrée. Aucun essai supplémentaire sans nouvelle décision.

### Résultat de l'essai

Essai exécuté une seule fois, le 9 octobre 2026, depuis le commit propre
`8de5c1ee689ab89d6105af1e672d80c6bd294405`, avec la commande ci-dessus et la fixture par
défaut. Résultat consigné tel qu'écrit par l'outil, sans retouche :
[`pat-125-native-trial.json`](pat-125-native-trial.json). Le flux brut et la sortie
d'erreur restent hors dépôt.

| Champ | Valeur consignée |
| --- | --- |
| Processus | code de sortie 0, 11,6 s, pas de dépassement |
| Version de Claude Code | 2.1.294 (événement d'initialisation et journal de l'enfant) |
| Version observée par le crochet | 2.1.294 pour 2.1.293 requis, statut `conforming`, aucun avertissement |
| Source du plugin | `this_checkout` ; profil listé à l'initialisation |
| Enfants | 1, type `foundry:routed-readonly-medium-haiku-5.5` |
| `conformity.profile` | `exact` |
| `conformity.model` | `exact` (`claude-haiku-5-5`) |
| `conformity.effort` | `exact` (`medium` ; `perTurnEffort` `medium`) |
| `conformity.host_version` | `conforming` |
| `conformity.fixture` | `divergent` |
| `verdict` | `not_conforming` |
| `not_established` | `["fixture"]` |

Lecture de la divergence, au niveau de l'instrument. Ce paragraphe ne vient pas du
fichier de résultat : il a été lu sur le flux brut par le coordinateur, puis relu sur ce
même flux par l'implémenteur. L'appel `Read` de l'enfant a bien renvoyé la ligne de la
fixture. La réponse de l'enfant ne la reproduit pas : elle dit ne pas afficher ce
« jeton » tel quel en invoquant une règle sur les secrets, et le parent a relayé cette
réponse. Avant cela, le parent avait d'abord répondu que l'agent était lancé en
arrière-plan. Le parent tourne avec les réglages de l'utilisateur chargés, comme pour
PAT-16 ; les instructions globales du mainteneur interdisent d'afficher des secrets, et
la fixture nomme sa valeur « token ». La valeur attendue est donc absente de la réponse
finale, ce que l'outil classe `divergent`.

- Établi par le résultat consigné : le profil lancé est celui de ce checkout ; le modèle
  exécuté observé est `claude-haiku-5-5` ; l'effort observé est `medium` ; la version de
  l'hôte a été observée par le crochet lui-même, conforme au minimum.
- Non établi : l'aller-retour de la fixture, donc le verdict de l'outil. L'explication
  ci-dessus est une lecture du flux brut, pas une mesure de l'outil.
- Non affirmé : aucune conclusion sur la qualité du modèle, aucun gain, aucune économie.
  Un seul lancement de 11,6 s ne dit rien de plus que ce tableau.

Décision appliquée par le coordinateur, selon PAT-ADR-0016 tel qu'écrit (« Si l'essai
échoue, ou si le modèle exécuté ou l'effort transmis ne sont pas observés conformes, le
titulaire Haiku 4.5 est conservé. Aucun essai supplémentaire n'est autorisé sans
nouvelle décision ») et selon la règle qu'un verdict déclaré à l'avance n'est pas
réinterprété après coup : le verdict consigné tient, aucun second essai n'est lancé, le
défaut n'est pas modifié.

Statut : **promotion non appliquée, en attente de la décision du mainteneur**, qui seul
peut choisir entre :

- (a) juger suffisants le modèle et l'effort observés, et promouvoir ;
- (b) autoriser un nouvel essai, unique, avec une fixture neutre ;
- (c) ne pas promouvoir.

Faiblesses connues de l'outil d'essai, pour un essai futur éventuel. La logique de
verdict n'a pas été modifiée et le résultat ci-dessus n'est pas recalculé.

- La fixture par défaut appelle sa valeur « token » dans `token.txt`. Sous des réglages
  utilisateur qui interdisent d'afficher des secrets, un modèle peut refuser de la
  restituer : le contrôle mesure alors cette règle, pas la compatibilité.
- L'hôte peut lancer l'agent en arrière-plan : le parent peut répondre avant le retour
  de l'agent. Le prompt par défaut ne lui demande pas d'attendre.
- Option préparée, séparée et non exécutée : `--neutral-fixture` écrit dans `marker.txt`
  un « marqueur de fixture » public (`marqueur-de-fixture-<8 chiffres>`), dit non secret
  dans le paquet, et demande au parent d'attendre la réponse finale de l'agent. Elle ne
  change ni la fixture par défaut, ni la règle de verdict, ni le fichier consigné ; son
  résultat porte `fixture_variant: "neutral"` et exige un autre dossier de travail et un
  autre fichier `--out`. Testée hors ligne seulement ; elle ne vaut autorisation de rien.

## Observation de production et retour arrière

Fixés par PAT-ADR-0016, à compter du merge de la promotion :

- Fenêtre : les dix premières issues livrées par Foundry sur l'hôte Claude après le
  merge, ou trente jours, au premier terme atteint.
- Référence : les dix dernières issues livrées avant le merge, lues dans les preuves
  d'acceptation et le tracker.
- Signaux, tous déjà présents : CI sur SHA exact, verdict de revue au premier passage,
  nombre de reprises et de cycles de correction, escalades enregistrées.
- Déclencheur immédiat : sur un hôte à la version requise, un profil `haiku-5.5`
  indisponible ou divergent, un modèle exécuté observé différent de `claude-haiku-5-5`,
  ou un effort transmis observé différent de `medium`.
- Déclencheur de régression : sur la fenêtre, le nombre d'issues ayant connu une revue
  bloquante au premier passage, une escalade ou une CI rouge sur le SHA livré dépasse
  d'au moins deux celui de la référence.
- Ces signaux sont en aval de l'exploration et ne lui sont pas attribuables de façon
  causale : ils justifient un retour arrière, pas une conclusion sur le modèle. Le
  mainteneur peut revenir en arrière à tout moment sans justification.

Retour arrière pour un projet, dans `.foundry/model-routing.json`, effort `null` écrit
explicitement :

```json
{"mappings": {"claude": {"economy": {"model": "haiku-4.5", "effort": null}}}}
```

La même forme sans la clé `effort` hérite de l'effort par défaut du tier (`medium` après
promotion) et est refusée avec un message qui demande d'écrire `"effort": null` : rien
n'est deviné. Retour arrière pour le produit : rétablir le défaut `economy` à Haiku 4.5
sans effort par une PR Foundry ordinaire, avec revue et CI sur SHA exact ; la
déclaration de `haiku-5.5` reste en place. Un retour arrière est une issue normale et ne
demande pas de nouvelle ADR tant que la valeur rétablie est celle de PAT-16. Les deux
formes projet et le défaut rétabli sont testés hors ligne
(`tests/test_claude_haiku55.py`).

Revue indépendante, CI sur SHA exact et cycle de vie Foundry restent obligatoires pour
la livraison.
