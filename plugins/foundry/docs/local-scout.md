# Local Scout — préprocesseur local, opt-in et sans autorité

`local-scout` est un préprocesseur **non fiable et non privilégié**, hors de la politique
de rôles et de tiers Foundry. Il peut proposer des hypothèses à partir d'un diff, de logs,
de tests ou d'une cartographie de code en deux passes, jamais sélectionner
un modèle cloud, modifier le dépôt, invoquer des outils, approuver un gate ou fusionner.
Le rôle cloud responsable reçoit des propositions et garde seul le jugement.

## Activation opérateur

Le mode est désactivé par défaut et n'a **aucun modèle implicite**. Son activation exige
deux choix indépendants et explicites. Le projet sélectionne d'abord, dans
`.foundry/local-scout.json`, l'identifiant exact d'un modèle déjà disponible dans le
runtime local de l'opérateur :

```json
{
  "version": 1,
  "model": "my-explicit-local-model"
}
```

Le runtime `local-scout` n'invente, ne résout ni ne force aucun alias de modèle ; une
éventuelle recommandation documentaire ne devient jamais un défaut runtime. Si le mode
est activé sans ce champ projet, le chargement échoue en
`LOCAL_SCOUT_POLICY_VIOLATION`. L'identifiant est conservé caractère pour caractère : des
espaces périphériques invalident la configuration au lieu d'être supprimés. L'opérateur
active ensuite le mode et choisit la
destination dans sa configuration de confiance (environnement, option Claude exportée,
ou `~/.config/foundry/config.env`) :

```sh
FOUNDRY_LOCAL_SCOUT_ENABLED=1
FOUNDRY_LOCAL_SCOUT_BASE_URL=http://127.0.0.1:11434
```

L'URL accepte uniquement `http://127.0.0.1:<port>` ou
`http://[::1]:<port>`, sans chemin, identifiants, requête ni fragment. Les noms DNS,
HTTPS, les proxys, redirections, headers d'authentification, streaming, tools et champs
fournisseur ne font pas partie du protocole. La requête directe utilise seulement le
sous-ensemble Chat Completions non-streaming (`model`, un message `user`, `stream:false`)
vers `/v1/chat/completions`; Foundry ne lit pas les variables de proxy.

Ce slice ne contacte **jamais** le cloud. `FOUNDRY_LOCAL_SCOUT_ON_FAILURE=error` est le
défaut; `cloud_economy` est un consentement utilisateur de confiance qui permet seulement
de matérialiser, dans le même processus, un plan de façade et le packet expurgé du
wrapper, jamais d'appeler le cloud. Aucune capability sérialisée n'est acceptée en retour.
Le nom historique `FOUNDRY_LOCAL_SCOUT_CLOUD_FALLBACK` est **déprécié** mais
reste compatible pendant toute la fenêtre Foundry 0.x ; il faut migrer vers
`FOUNDRY_LOCAL_SCOUT_ON_FAILURE` avant Foundry 1.0. Un canonical direct explicite
(`error` ou `cloud_economy`) gagne. Sous Claude, le `error` canonical injecté par défaut
par le manifest ne peut pas être distingué d'un choix utilisateur : un legacy `1` reste
donc effectif dans cette coexistence, tandis que `cloud_economy` canonical gagne toujours.
Le fichier projet ne peut définir ni l'un ni l'autre.

### Confidentialité et frontière de confiance

En chemin local normal, Foundry envoie la capture expurgée uniquement à l'adresse
loopback explicitement choisie ; il n'upload aucun payload, n'utilise aucun proxy et ne
journalise ni capture, prompt, réponse, URL ou identifiant de modèle. Le packet borné et
expurgé peut ensuite être remis au rôle cloud responsable par le workflow qui l'a demandé
— c'est la finalité explicite du prétraitement, pas un upload autonome du provider local.
Le fallback `cloud_economy` ne transmet rien lui-même : il matérialise seulement un plan
et un packet assaini que l'hôte doit exécuter explicitement.

La télémétrie passive est une frontière séparée : elle ne reçoit aucun contenu de
payload et n'influence jamais ce chemin. Foundry confine son propre client et refuse
toute autorité à la sortie locale, mais ne prétend pas sandboxer le daemon ou le modèle
administré par l'opérateur.

Le même fichier projet peut **réduire** les plafonds ; il ne peut pas activer le mode,
choisir l'URL ou autoriser une dépense cloud :

```json
{
  "version": 1,
  "model": "my-explicit-local-model",
  "limits": { "max_response_bytes": 32768 }
}
```

## Délais locaux : défauts, confiance et plafond dur

Les défauts produits restent volontairement conservateurs : connexion **2 s** et durée
totale **8 s**. Un opérateur de confiance peut les élargir, sans activer le mode ni
choisir une destination, avec des valeurs finies strictement positives :

```sh
FOUNDRY_LOCAL_SCOUT_CONNECTION_TIMEOUT_SECONDS=6
FOUNDRY_LOCAL_SCOUT_TOTAL_TIMEOUT_SECONDS=30
```

Les plafonds absolus sont codés, non désactivables et ne peuvent jamais être dépassés :
**10 s** pour la connexion et **120 s** pour toute la transaction. La connexion doit
rester inférieure ou égale à la durée totale. Le maximum total de 120 s est celui du
protocole FOUNDRY-35 : il laisse mesurer les modèles 20–27B en cold/warm sans confondre
le plafond produit historique de 8 s avec leur capacité intrinsèque.

Dans `.foundry/local-scout.json`, `limits` ne peut qu'abaisser les budgets déjà résolus
depuis la configuration de confiance ; tenter de les élargir est une
`LOCAL_SCOUT_POLICY_VIOLATION` expurgée. Booléens, texte, zéro, négatifs, NaN, Infinity
et dépassements sont refusés. Ces deux variables de délai sont indépendantes de
l'activation, de l'URL, du modèle et de l'autorisation de fallback : les définir seules
ne provoque aucun appel et n'autorise aucune dépense.

Le doctor peut lire uniquement les deux budgets effectifs numériques via
`local_scout_budget_view`; cette vue ne retourne ni URL, ni modèle, ni état d'activation
ou de fallback, et n'invoque jamais le modèle.

## Contrat de benchmark FOUNDRY-35

Le fixture versionné
`tests/fixtures/local-scout-benchmark-contract.json` fixe le protocole
candidate-agnostique utilisé par FOUNDRY-35. Ce n'est pas un fichier de résultats :
`foundry.local_benchmark.load_benchmark_contract` le parse strictement, puis
`validate_benchmark_record` rejette tout résultat futur incomplet ou incohérent et
retourne les agrégats recalculés depuis les runs bruts. Le parseur refuse notamment les
champs inconnus, les doublons JSON, `NaN`/`Infinity`, les types implicites et une version
de contrat non reconnue.

Le résultat doit épingler le modèle et son SHA-256, la quantification, le runtime, sa
version et son instance, le SHA-256 du template de prompt, l'identité et les capacités
matérielles, les plafonds de connexion et de transaction effectifs, ainsi que les
paramètres de requête et génération. Il ne comporte ni secret, ni URL d'endpoint, ni
prompt brut. Chaque préparation et chaque run répète le digest canonique de ce contexte,
ce qui interdit de mélanger des mesures obtenues avec des candidats, runtimes, limites,
templates ou paramètres différents.

Le champ versionné `repetitions_per_state` fixe la couverture exacte exigée pour chaque
état ; le parseur v1 accepte une valeur de 3 à 100. Le fixture livré fixe cette valeur à
trois, sans faire de « trois répétitions » une constante universelle du format v1. La
séquence distingue mécaniquement les états :

- `cold_start` : avant chaque run chronométré, l'opérateur ou le runtime fournit une
  nouvelle préparation `external_unloaded_reset`, exclue des métriques et attestant que
  le modèle cible n'est pas chargé. Le run peut donc inclure le coût de chargement ;
- `warm_run` : aucun appel de préparation exclu ne qualifie cet état. Chaque run suit
  immédiatement un run **mesuré** attesté chargé après exécution, sur le même contexte.
  Cette continuité dépend de l'attestation de chargement, pas du résultat de la tâche :
  une expiration produit peut donc précéder la répétition suivante si elle atteste
  `loaded_after_timing=true`. Son identité et l'intervalle borné sont enregistrés ;
- `already_loaded` : une préparation `external_preload`, exclue du chronométrage, précède
  toute la série. Chaque run atteste l'état chargé avant mesure et référence cette même
  identité de préparation, jamais un run mesuré.

Foundry ne réalise aucune de ces préparations : les identités et attestations sont des
preuves déclaratives produites par l'opérateur/runtime externe. Le validateur n'installe,
ne télécharge, ne démarre, n'arrête et ne supervise ni daemon ni poids.

Pour chaque état, `timeout_rate`, `median_seconds` et `p95_seconds` (nearest-rank) sont
recalculés sur les durées brutes finies et positives ou nulles ; les valeurs rapportées
doivent être identiques à six décimales. Une expiration du plafond produit doit avoir
`timed_out=true`, la classification `configured_product_limit_reached` et un
`timeout_kind` strict (`connection` ou `total`) qui sélectionne le plafond positif dans
`metadata.effective_limits`. La durée observée doit atteindre ce plafond. Pour conserver
la durée de bout en bout utile aux agrégats sans exiger une égalité d'horloge irréaliste,
le contrat v1 autorise au plus 0,25 seconde de surcoût d'observation au-delà du plafond
sélectionné ; cette borne est fixée dans le fixture et le code, jamais dans le résultat.
Une durée antérieure au plafond, au-delà de cette fenêtre, ou un type de timeout absent
ou contradictoire est rejeté. Un run non expiré doit avoir `timeout_kind=null` et reste
borné par le plafond total. Une expiration produit ne peut jamais être étiquetée
`model_failure`. Les données synthétiques des tests prouvent seulement le validateur.

La campagne v4 réellement exécutée est publiée séparément avec ses preuves brutes,
agrégats historiques, addendum normatif et décision fail-closed. Elle ne promeut aucun
modèle : les quatre candidats ont produit 0/12 packets produit valides, échoué leurs
12/12 pipelines downstream contre 12/12 contrôles Terra/medium réussis, réduit l'input
d'environ 0,32 % seulement et n'avait pas de prix public immuable applicable. Les
latences, tokens, coûts nullables, mémoire, retries, limites et prochain test sont résumés
dans [`release-0.7.0.md`](release-0.7.0.md) et sourcés dans
[`FINAL-RECOMMENDATION-v4.md`](../benchmarks/foundry-35/FINAL-RECOMMENDATION-v4.md).

## Mode diff

Les deux hôtes appellent la même frontière partagée :

```sh
python3 tooling/foundry_cli.py local-scout diff --diff-file /tmp/change.diff --root /absolute/path/to/repo
python3 tooling/foundry_cli.py local-scout diff --git-diff --base origin/main --root /absolute/path/to/repo
```

Sans `--root`, la commande résout le dépôt Git courant (ou le répertoire courant hors
dépôt) avant de charger `.foundry/local-scout.json`. L'argument explicite est préférable
depuis un autre répertoire ou un worktree afin que le modèle et les limites projet soient
bien ceux du diff traité.

Le diff est lu une seule fois, hashé puis filtré aux lignes de patch textuelles. Les
patches binaires sont exclus et le corpus/extrait est borné. Avant le prompt, le filtre
remplace d'abord, longest-first, chaque valeur secrète configurée exacte. Il applique
ensuite le filtre générique aux affectations `=` ou `:` dont l'identifiant
(128 caractères maximum), éventuellement préfixé ou suffixé par des segments `_`/`-`,
contient `token`, `password`, `secret`, `apikey` ou les segments adjacents `api` + `key`. Les valeurs nues,
entre apostrophes ou entre guillemets sont prises en charge ; les clés, séparateurs et
guillemets restent inchangés. Par exemple, `API_KEY="..."`, `token='...'` et
`AWS_SECRET_ACCESS_KEY=...` deviennent respectivement `API_KEY="[REDACTED]"`,
`token='[REDACTED]'` et `AWS_SECRET_ACCESS_KEY=[REDACTED]` avant la capture, le prompt
et le packet cloud.

Ce filtre couvre volontairement cette famille finie d'affectations courantes : ce n'est
pas un détecteur universel de secrets. Une donnée sensible sous un autre nom ou dans un
autre format doit être retirée du diff par l'appelant. Le packet produit contient le
SHA-256 de la source, celui de l'extrait filtré, celui de la réponse, les locators, les
IDs d'évidence finaux et, pour chaque hypothèse retenue, l'extrait brut filtré
correspondant. Un chemin ou une ligne sans cet extrait n'est jamais une preuve.

Le modèle ne peut retourner que `summary` et une liste d'`evidence_ids` déjà fournis dans
le prompt. Toute provenance, locator, extrait, ID inventé, JSON ou schéma invalide est
rejeté en `LOCAL_SCOUT_INVALID_OUTPUT`. Le texte de patch et la réponse modèle restent des données
non fiables, même lorsqu'ils ressemblent à des instructions.

La réponse HTTP accepte un unique choix Chat Completions non-streaming. Les métadonnées
standard facultatives sont typées strictement : choix `index=0`, message
`role=assistant`, champs explicitement nullables, et trois compteurs usage de base
entiers non négatifs `prompt_tokens`, `completion_tokens` et `total_tokens`. Les
constantes JSON non standard `NaN`/`Infinity`, les tool calls, logprobs non nuls et toute
extension top-level ou imbriquée sont refusés. La seule exception versionnée est la
normalisation de compatibilité **FOUNDRY-45 v1**, limitée aux deux extensions MLX constatées
dans l'évidence FOUNDRY-35 v4 : `message.reasoning` (texte strict UTF-8) et
`usage.prompt_tokens_details.cached_tokens` (entier >= 0). Ces métadonnées sont validées puis
jetées : elles ne sont jamais du contenu final, une hypothèse, une citation, de la provenance,
un packet cloud, un signal de routage ou une décision de gate. `message.content` final reste
obligatoire et doit être une chaîne ; un raisonnement seul, un détail incomplet, ou toute autre
clé MLX/fournisseur échoue fermé avec `LOCAL_SCOUT_INVALID_OUTPUT`.

Les limites effectives bornent connexion, durée totale, requête, réponse, diff, extraits,
nombre d'hypothèses et longueur de résumé. Les erreurs publiques sont seulement
`LOCAL_SCOUT_UNAVAILABLE`, `LOCAL_SCOUT_INVALID_OUTPUT`,
`LOCAL_SCOUT_POLICY_VIOLATION` ou `LOCAL_SCOUT_STALE_INPUT`, sans URL, prompt,
réponse distante, credential ni header dans leur message. Passer
`--expected-sha256 <digest>` détecte un diff devenu obsolète avant l'appel local.

Foundry ne télécharge, n'installe, ne démarre ni ne supervise aucun modèle ou runtime.

## Mode code : deux passes locales, déterminisme entre les deux

Le mode code est une autre vue du même provider local opt-in. Il n'est ni un rôle
Foundry, ni un scout autonome, ni une source de vérité. Son objectif est de réduire le
contexte cloud en demandant au modèle local de sélectionner puis classer des preuves,
tandis que le wrapper déterministe reste seul propriétaire des lectures, digests,
locators et extraits.

```text
signaux de chemins déterministes
        ↓
manifest trié, borné, sans corps de fichier
        ↓ appel local 1
chemins du manifest + termes littéraux bornés
        ↓
confinement no-follow + lecture UTF-8 + expurgation + copies privées + rg -F
        ↓
bundle gelé, borné, hashé, IDs d'évidence du wrapper
        ↓ appel local 2
hypothèses citant uniquement ces IDs
        ↓
packet cloud avec extraits bruts filtrés, locators, digests et limites
```

La commande partagée Claude/Codex est :

```bash
python3 tooling/foundry_cli.py local-code --query-file /tmp/goal.txt --base origin/main --root /absolute/repo
```

Le wrapper collecte par défaut les chemins de `git ls-files`, `git status` et
`git diff --name-only --no-ext-diff`. Un producteur déterministe peut ajouter des
candidats déjà calculés par arbre ciblé, symboles, imports, références ou `rg` via un
fichier borné qui ne contient que des listes de chemins relatifs :

```json
{
  "symbols": ["src/router.py"],
  "imports": ["src/policy.py"],
  "references": ["tests/test_router.py"],
  "rg": ["docs/model-routing.md"]
}
```

```bash
python3 tooling/foundry_cli.py local-code --query-file /tmp/goal.txt --root /repo --signals-file /tmp/path-signals.json
```

Le premier prompt contient seulement l'objectif expurgé, les chemins, les signaux qui
les ont proposés et leur taille de fichier. Aucun corps n'est lu avant la sélection.
La réponse doit être exactement `paths` + `search_terms`. Les chemins hors manifest,
absolus, avec `..`, sensibles, manquants, dupliqués ou traversant un symlink sont
refusés. Les termes sont des chaînes littérales : une valeur commençant par `-`, trop
longue, dupliquée ou contenant un caractère de contrôle est refusée. Même une chaîne
qui ressemble à une commande reste une donnée passée à `rg -F` par argv, jamais à un
shell.

Après sélection, le wrapper ouvre chaque composant sans suivre les symlinks, vérifie
l'inode avant/après ouverture, exige un fichier régulier UTF-8 et expurge les secrets
portables avant toute copie ou recherche. Les recherches `rg -F` s'exécutent uniquement
sur les copies privées gelées. Les sources originales sont relues avant le second appel,
puis avant la production du packet cloud ; toute modification, disparition ou
substitution devient `LOCAL_SCOUT_STALE_INPUT`.

Les plafonds v1 codés sont : 4 096 candidats, manifest de 256 fichiers / 16 KiB,
chemin de 128 caractères, objectif de 1 200 caractères, 10 chemins sélectionnés,
6 termes de 128 caractères, 64 KiB par fichier, bundle d'extraits de 4 KiB,
20 preuves de 1 KiB maximum, 32 KiB de sortie `rg` et 32 matches par terme. Ils laissent
une marge déterministe sous le plafond partagé de requête locale de 24 KiB, y compris
pour la métadonnée de provenance. Le packet expose ces plafonds effectifs avec les
extraits réellement cités.

Le second modèle ne retourne que `summary` et des `evidence_ids` existants. Il ne peut
inventer ni chemin, locator, digest, extrait ou provenance. Un résumé sans citation est
refusé. Le packet final reste `untrusted=true` et `proposal_only` : le rôle cloud juge
les extraits bruts filtrés, pas le résumé local. Une exécution comporte exactement deux
appels locaux. Aucun cycle adaptatif n'est possible ; conformément à ADR-0003, une
demande de détail est une nouvelle exécution explicite et plus étroite.

## Captures V1 et fallback FOUNDRY-33

Les modes `diff`, `logs` et `tests` acceptent seulement une capture immutable, filtrée,
expurgée et bornée avant l'appel local. Les locators sont typés (`diff:Lx-Ly`,
`logs:Lx-Ly`, `tests:Lx-Ly`) et le wrapper matérialise extraits filtrés, digests et
provenance. Chaque composant de chemin, lexical puis résolu, est tokenisé par ponctuation
et séparateurs. Les tokens contrôlés `secret(s)`, `credential(s)`, `token(s)`,
`password(s)`, `key`, `keychain`/`keystore`, les paires `api-key`, `client-secret`,
`private-key`/`secret-key`, les variantes de clés `id_*`, `.pem`/`.key` et `.env` sont
refusés avant l'ouverture. Cette reconnaissance par tokens évite les faux positifs de
sous-chaîne comme `monkey.py`, `secretary.txt`, `tokenizer.log` ou `keynote.txt`. Les
affectations usuelles et valeurs secrètes configurées exactes sont remplacées par
`[REDACTED]`; l'exact configuré est traité longest-first **avant** l'affectation générique,
donc un secret multi-mots non cité ne peut laisser fuiter son suffixe. Le token tracker
résolu par la chaîne portable environnement/options Claude/keychain/config de l'ADR-0005
rejoint aussi cet ensemble d'expurgation. Les contenus et valeurs ne sont jamais
journalisés. Les fonctions de capture partagées résolvent elles-mêmes ces sources
portables ; une liste supplémentaire fournie par un appelant est fusionnée et ne peut
jamais les remplacer. Les charges hors plafond sont refusées avant l'appel.

`ON_FAILURE=error` est le défaut. `FOUNDRY_LOCAL_SCOUT_ON_FAILURE=cloud_economy`, lu
uniquement depuis une configuration utilisateur de confiance (ou l'alias compatible
`FOUNDRY_LOCAL_SCOUT_CLOUD_FALLBACK=1`), autorise le même processus à produire un plan
`source=cloud_fallback` pour `LOCAL_SCOUT_UNAVAILABLE` ou
`LOCAL_SCOUT_INVALID_OUTPUT`. Le caller sélectionne son hôte dès la commande initiale :

```bash
python3 tooling/foundry_cli.py local-scout diff --git-diff --base origin/main --root /repo --fallback-host claude [--issue FOUNDRY-123]
python3 tooling/foundry_cli.py local-scout diff --git-diff --base origin/main --root /repo --fallback-host codex [--issue FOUNDRY-123]
```

Il n'existe plus de commande de replay `fallback-plan` : un JSON auto-cohérent ne peut
ni prouver le consentement ni revendiquer `source=wrapper`. En cas d'échec éligible, le
processus possède encore la configuration de confiance et la capture enregistrée. Il
réduit déterministiquement les preuves au budget réel de 4 000 caractères, recalcule
IDs/digests/provenance et marque la troncature. Le packet ne contient jamais la source
brute non filtrée ni les hypothèses locales.

Codex passe ce packet à `codex_spawn_plan`, avec plancher d'issue normal et, lorsque la
télémétrie est activée, sa capability de completion. Le plan de fallback FOUNDRY-35 gelé
pour Claude conserve l'alias de compatibilité déterministe `foundry:scout` ; son hook
résout l'invocation réelle vers le contrat `scout` inchangé de Lupin avec le même
`claude_route_plan` partagé, de sorte que le plan visible et la route exécutée ont
la même résolution. Avec un plancher dynamique actif, l'enveloppe Claude épingle le tier
et l'effort, puis laisse cette façade commune résoudre le modèle : un modèle direct ne
peut pas contourner ou être classé contre le plancher. Les observations explicites
`--available-model` et les signaux de profil sont réservés au chemin Codex ; Claude les
résout depuis son propre environnement hôte. Le caller exécute l'unique objet `spawn`
exactement une fois. La création de ce fallback cloud sous Claude suit donc ADR-0006 et
peut être observée normalement ; l'échec
local préalable ne consomme ni tentative ni escalade. `POLICY_VIOLATION` et `STALE_INPUT`
arrêtent sans plan.

Le doctor affiche `disabled`, `configured`, `available`, `unavailable` ou `invalid
policy`. Son CLI effectue uniquement une connexion TCP loopback bornée pour distinguer
available/unavailable; il ne réalise ni HTTP, ni complétion, ni démarrage de daemon, ni
exposition d'URL, modèle ou secret.
