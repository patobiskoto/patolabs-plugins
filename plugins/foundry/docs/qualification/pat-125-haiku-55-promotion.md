# Décision de promotion Claude Haiku 5.5 (PAT-125)

Statut : promotion demandée et cadrée par PAT-ADR-0016 (proposée, acceptée au merge) ;
**défaut non modifié à cette révision**. La déclaration de `haiku-5.5`, ses profils, la
règle de version minimale et l'outil d'essai sont livrés ; le défaut `economy` reste
Haiku 4.5 sans effort tant que le résultat de l'essai natif n'est pas consigné ci-dessous.
Cette note consigne la décision humaine. Elle ne crée aucune preuve ni receipt.

## Approbation et périmètre

Le 7 octobre 2026, le mainteneur a demandé la promotion de Claude Haiku 5.5. Le
8 octobre 2026, il a retenu : installer et promouvoir, effort `medium`, retour arrière,
ADR, merge après revue et CI. PAT-ADR-0016 applique le régime du candidat moins cher de
FOUNDRY-ADR-0019.

| Tier | Avant (PAT-16) | Après promotion | Rôle principal |
| --- | --- | --- | --- |
| economy | `haiku-4.5`, effort `null` non applicable | `haiku-5.5` (`claude-haiku-5-5`), effort `medium` | scout |

Le changement porte sur le tier : il vaut aussi pour tout rôle non-gate qui retombe sur
`economy` par le fallback descendant existant. Les tiers `balanced`, `frontier` et
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
décrites dans [`model-routing.md`](../model-routing.md), section PAT-125. Conséquence
cassante à la promotion : un hôte antérieur à 2.1.293 perd le tier `economy` par défaut
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

Non exécuté à cette révision. À consigner ici avant tout changement du défaut : date,
référence du source, version de Claude Code, verdict et renvoi vers
`pat-125-native-trial.json`.

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
