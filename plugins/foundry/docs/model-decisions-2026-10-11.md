# Décisions et retours de configuration — PAT-17, 11 octobre 2026

Statut documentaire : ajouté. Cette page applique le corps PAT-17 approuvé le
11 octobre 2026 : décision d'ensemble fondée sur les observations existantes et
essais de retour par configuration. Les mentions d'un futur benchmark PAT-17 dans
les rapports historiques décrivent leur mandat antérieur ; elles restent intactes.
Une campagne comparative payante demanderait une autorisation distincte.

## Décision par hôte et rôle

« Adopté » ci-dessous reprend une promotion déjà décidée, sans nouvelle promotion.
Le profil de politique courant vient de `DEFAULT_MAPPINGS` et `ROLE_DEFAULTS` dans
[`routing.py`](../tooling/foundry/routing.py). La portée de chaque observation reste
celle du rapport cité, à ses coordonnées historiques ; la disponibilité sur un
autre client ou provider reste inconnue. Les quatre rôles de chaque hôte sont les
mêmes ensembles que ceux du tableau de retour ci-dessous.

| Hôte | Rôle | Profil courant | Décision et preuve existante |
| --- | --- | --- | --- |
| Codex | exploration (`scout`) | `gpt-6-luna` / low | Adopté techniquement, PAT-ADR-0010 ; slot terminé, identité et effort observés dans [PAT-15](qualification/pat-15-codex-qualification-v1.md). |
| Codex | implémentation (`implementer`) | `gpt-6.1-sol` / medium | Adopté techniquement, PAT-ADR-0010 ; slot terminé, identité et effort observés dans [PAT-15](qualification/pat-15-codex-qualification-v1.md). |
| Codex | relecture (`reviewer`) | `gpt-6.1-sol` / high | Adopté techniquement, PAT-ADR-0010 ; revue mergeable, ancienne AC économique non couverte, dans [PAT-15](qualification/pat-15-codex-qualification-v1.md). |
| Codex | architecture (`architect`) | `gpt-6.1-sol` / max | Adopté techniquement, PAT-ADR-0010 ; slot terminé, identité et effort observés dans [PAT-15](qualification/pat-15-codex-qualification-v1.md). |
| Claude Code | exploration (`scout`) | `haiku-5.5` / medium | Adopté sous observation de production, PAT-ADR-0016 ; [PAT-125](qualification/pat-125-haiku-55-promotion.md) conserve le premier verdict non conforme et le second conforme, deux fumées de compatibilité, sans qualification de qualité. |
| Claude Code | implémentation (`implementer`) | `sonnet-5.5` / medium | Adopté techniquement, PAT-ADR-0013 ; complément Read/Edit sur copie isolée, échec initial conservé, dans [PAT-16](qualification/pat-16-claude-default-promotion.md). |
| Claude Code | relecture (`reviewer`) | `opus-5.5` / high | Adopté techniquement, PAT-ADR-0013 ; complément ayant lu le diff, 14 AC passés et 2 non couverts à sa référence, refus initial conservé, dans [PAT-16](qualification/pat-16-claude-default-promotion.md). |
| Claude Code | architecture (`architect`) | `opus-5.5` / high | Adopté techniquement, PAT-ADR-0013 ; fixture passée à la référence du premier lot dans [PAT-16](qualification/pat-16-claude-default-promotion.md). |

Aucun de ces rapports n'établit une économie comparative de facture ou de quota
pour les huit profils courants. Le fondement « moins cher » de Haiku 5.5 est le prix
catalogue de PAT-ADR-0016, pas une mesure par tâche ; son effort `medium` n'est pas
mesuré ([PAT-125](qualification/pat-125-haiku-55-promotion.md)). Les mesures
[PAT-19](qualification/pat-19-local-first-bilan.md) comparent d'autres bras et tâches,
et la [ventilation PAT-129](qualification/pat-19-cost-breakdown-v1-x5compare-1.json)
porte sur leurs journaux. Ces ensembles ne sont pas ceux des huit profils : aucun
classement ni gain n'en est transféré ici. Qualité générale, économie comparative et
portabilité non observée restent inconnues.

## Retours réellement exercés par configuration

Résultat observé : les huit retours ci-dessous ont passé la résolution CLI et la
construction d'invocation le 11 octobre 2026 dans des dossiers temporaires. Le
[rapport JSON](qualification/pat-17-config-rollback-2026-10-11.json) porte le HEAD de
base du travail ; le [vérificateur](../scripts/check_model_rollback.py) est livré par
cette révision, sans modifier le runtime. Il utilise le vrai CLI
`python3 -m foundry.routing resolve`, puis `codex_spawn_plan`, ou
`claude_route_plan` et `claude_invocation_binding` de
[`routing_facades.py`](../tooling/foundry/routing_facades.py). Aucun résultat simulé
n'est une observation native. Aucun hôte modèle n'a été lancé : les identités
exécutées, l'acceptation backend et les efforts observés restent inconnus.

| Hôte | Rôle / tier | Profil précédent essayé | Invocation construite | Résultat de configuration |
| --- | --- | --- | --- | --- |
| Codex | scout / economy | `gpt-5.6-luna` / low | explorer, modèle et effort explicites | passé |
| Codex | implementer / balanced | `gpt-5.6-terra` / medium | worker, modèle et effort explicites | passé |
| Codex | reviewer / frontier | `gpt-5.6-sol` / high | reviewer, modèle et effort explicites | passé |
| Codex | architect / apex | `gpt-5.6-sol` / max | default, modèle et effort explicites | passé |
| Claude Code | scout / economy | `haiku-4.5` / null | `routed-readonly-none-haiku-4.5`, `claude-haiku-4-5`, sans effort | passé |
| Claude Code | implementer / balanced | `sonnet-5` / medium | `routed-worker-medium-sonnet-5`, `claude-sonnet-5` | passé |
| Claude Code | reviewer / frontier | `opus-5` / high | `routed-readonly-high-opus-5`, `claude-opus-5` | passé |
| Claude Code | architect / apex | `fable-5` / high | `routed-readonly-high-fable-5`, `claude-fable-5` | passé |

Le scout Claude revient au titulaire immédiatement précédent de PAT-125, Haiku 4.5
sans effort. Les autres rôles Claude reviennent aux profils précédant PAT-16 ; les
profils Codex reviennent à la référence GPT-5.6 de PAT-15. L'effort `null` est écrit
explicitement, comme l'exige [PAT-125](qualification/pat-125-haiku-55-promotion.md) ;
il ne restaure pas l'ancien libellé legacy-low de l'ADR-0006. Sources des mappings :
[exemple Codex](../examples/codex-gpt-5.6-rollback.json),
[nouvel exemple Claude](../examples/claude-pre-promotion-rollback.json),
[PAT-16](qualification/pat-16-claude-default-promotion.md) et PAT-125.

Fable était exclu de la qualification Pro sans crédits supplémentaires de PAT-16 :
son retour de configuration passe, son utilisation native sur ce compte n'est pas
qualifiée. Aucun retour natif impossible n'est constaté par cet exercice ; leur
possibilité demeure inconnue pour les huit lignes. Un refus backend ne se corrige
pas en présentant un plan comme un appel réussi. Le rollback d'une version du plugin
reste porté par PAT-162.

### Procédure exercée sur copie

Depuis la racine du dépôt, cette commande reproduit les huit exercices ; `--out`
désigne un fichier de résultat jetable choisi par l'opérateur :

```bash
python3 plugins/foundry/scripts/check_model_rollback.py --out /tmp/pat-17-config-rollback.json
```

Le vérificateur crée une copie de politique avec une surcharge de rôle et un mapping
de l'autre hôte, conserve ses octets, fusionne seulement le tier de la ligne depuis
l'exemple, puis vérifie la résolution et le plan construit. Il restaure ensuite les
octets d'origine et la route initiale ; il vérifie aussi un état initial sans fichier,
restauré à l'absence. Les huit lignes préservent les clés non concernées. Le reviewer
Codex utilise une claim sur un diff de fixture, jamais une revue du code produit.
Le vérificateur lit aussi le HEAD par `git rev-parse HEAD` et écrit le JSON au
chemin `--out` ; sources et procédure exacte :
[`exercise` et `main`](../scripts/check_model_rollback.py).

Cette procédure a été exécutée sur copies avec succès ; elle ne modifie ni la
politique du projet réel ni le cache installé. Pour préparer un retour opérateur,
la section du tier correspondant dans l'exemple doit être fusionnée dans une copie
de la politique existante, avec ses autres clés conservées ; remplacer tout le
fichier par l'exemple supprimerait ces clés. Appliquer cette copie au projet réel et
observer une invocation native reste une action distincte de cet exercice.

## Refus Codex PAT-164

Cause observée : le CLI 0.155.1 a refusé `gpt-6.1-sol` avec une connexion ChatGPT,
alors que la session prenait son modèle dans la configuration Codex principale.
Le même compte a vu ce modèle dans le cache du CLI 0.160.0, et la même demande a
ensuite démarré sous 0.162.1. Cause déduite : ancien CLI ; cause backend précise et
version minimale inconnues. Une délégation Foundry native n'a pas été exercée.
Source détaillée : [note opérationnelle PAT-164](model-routing.md#codex-invocation-façade).

Ces observations ne montrent pas de défaut du resolver Foundry : aucun mapping
n'est corrigé par PAT-17. Les essais de configuration ci-dessus ne remplacent pas
les observations natives de PAT-164 et ne prouvent pas la disponibilité actuelle
d'un compte. Les vérifications de version et de liste restent celles de la note
opérationnelle ; PAT-17 ne les relance pas.

## Validation et statuts R5

- `DEFAULT_MAPPINGS` et profils courants : documentation actualisée par le tableau,
  sans changement des constantes ; `model-routing.md` pointe vers cette décision.
- Configuration `mappings.<host>.<tier>.model/effort` : nouvel exemple Claude et
  procédure exercée, exemple Codex conservé ; aucune nouvelle clé de politique.
- Surface `scripts/check_model_rollback.py --out` : ajoutée et documentée ici avec
  ses écritures temporaires et son résultat JSON, sans appel natif.
- Rapport de décision et rapport JSON : ajoutés ; limites natives et économiques
  explicites. Pages de migration et rapports historiques conservés à leurs dates.
- `CHANGELOG` : entrée Unreleased ajoutée. Contrats AGENTS/CLAUDE inchangés.

Vérification locale : `test_model_rollback.py`, `test_codex_rollback.py`,
`test_claude_haiku55.py` et `test_process_contract.py` : 129 tests passés. Ces tests
vérifient aussi le maintien des planchers et le refus d'un titulaire indisponible
pour les gates ; ils ne sont ni une CI du futur SHA ni une nouvelle qualification.
