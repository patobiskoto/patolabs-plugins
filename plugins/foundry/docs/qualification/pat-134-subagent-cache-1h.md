# PAT-134 — Cache de prompt à « 1 heure » pour les sous-agents Sonnet 5.5 : essai natif, règle d'observation et retour arrière

PAT-134. Cadre : FOUNDRY-ADR-0015 (lecteur hors ligne des journaux de session de l'hôte : compteurs de tokens, alias de modèle, horodatage,
identifiant de session ; champs au-delà nommés), FOUNDRY-ADR-0008 (modèle, effort et politique de contexte découplés), FOUNDRY-ADR-0019 (par
analogie : une configuration moins chère s'adopte sous observation, avec retour arrière), AGENTS.md R5, R6, R7. Suite de
[`pat-133-cache-ttl-interactive-v1.md`](pat-133-cache-ttl-interactive-v1.md) (dont le fichier d'agrégats et ceux de PAT-132 ne sont **pas
modifiés**). Décision du mainteneur (2026-10-10) : « Ok go et on enchaine » sur la proposition de passer à « 1 heure » les seuls profils
d'agents Sonnet 5.5, adoptée sous observation, sans banc ; un essai natif borné est couvert par son accord antérieur pour un banc.

Légende. **[doc]** : fait de la documentation d'Anthropic **lu par le coordinateur le 2026-10-09** (`code.claude.com/docs/en/sub-agents` et
`prompt-caching`), **non revérifié par l'outillage**. **[code]** : lu dans le dépôt. **[essai]** : à lire dans le fichier de résultat de l'essai
natif (voir plus bas ; **en attente** tant qu'il n'est pas consigné).

## Ce que dit la documentation [doc]

- Un profil d'agent peut porter, **dans une table `experimental` et non au niveau supérieur du frontmatter**, `cacheTtl: 5m` ou `cacheTtl: 1h`.
  Claude Code 2.1.248 ou plus est requis.
- `1h` est **ignoré tant que l'abonnement puise dans des crédits d'usage**.
- Précédence, du plus fort au plus faible : `FORCE_PROMPT_CACHING_5M`, la variable d'environnement du seau, le réglage du seau
  (`subagentPromptCacheTtl`), puis le `cacheTtl` du frontmatter du sous-agent, puis `ENABLE_PROMPT_CACHING_1H`, puis le défaut. Un réglage de
  l'utilisateur l'emporte donc sur le profil ; Foundry n'y touche pas.

## Ce que le changement est, et n'est pas

- Seuls les **dix profils versionnés Sonnet 5.5** (`routed-{readonly,worker}-{low,medium,high,xhigh,max}-sonnet-5.5`) portent
  `experimental: {cacheTtl: 1h}`. Aucun autre profil ne change (Opus, Haiku, Fable, Sonnet 5 historique, profils génériques) ; ni le routage, ni
  les modèles, ni les efforts, ni les réglages de l'utilisateur ; aucune clé de configuration nouvelle.
- Le changement est porté par **le dernier commit de la branche**, séparé des outils et de cette page, pour pouvoir être écarté si l'essai
  natif n'est pas conforme. Le contrôle déterministe des profils préchargés (`claude_pin_profile_text`, comparé octet pour octet par
  `claude_invocation_binding`) attend exactement ce champ pour ces dix profils et rejette tout profil divergent : champ manquant sur un profil
  Sonnet 5.5, champ sur tout autre profil, autre valeur, autre clé sous `experimental` (`tests/test_claude_profiles.py`).
- **Aucun gain n'est annoncé.** La mesure PAT-133 (trois sessions, dollars sur une seule, simulation) est un argument pour essayer, pas une preuve.
  **Effet sur le quota d'abonnement : inconnu.**

## Essai natif borné (hors tests, lancé à la main par le coordinateur)

Outil : `python3 -m foundry.claude_profile_trial --cache-ttl-trial` (option nouvelle ; le fixture par défaut, la règle de verdict de PAT-125 et
ses résultats enregistrés sont inchangés). **Une** exécution `claude -p` (jamais sous `sandbox-exec`, environnement fermé de R6, arrêt à vingt
minutes), qui charge le plugin de CE dépôt (`--plugin-dir`) depuis un fixture isolé hors dépôt dont la politique de projet met `economy` sur
`sonnet-5.5` / `low` et `balanced` sur `opus-5.5` / `medium`. Le parent lance **l'un après l'autre** deux sous-agents par les rôles logiques :

| Sujet | Rôle logique | Profil attendu | `cacheTtl` demandé | Classe attendue dans le journal |
|---|---|---|---|---|
| modifié | `foundry:lupin` | `routed-readonly-low-sonnet-5.5` | `1h` | écriture « 1 heure » seule |
| témoin | `foundry:eiffel` | `routed-worker-medium-opus-5.5` | absent | écriture « 5 minutes » seule |

Chacun lit deux fichiers du fixture (au moins deux requêtes). **Limite** : le témoin est un profil d'exécution (Opus 5.5) et le sujet un profil en
lecture seule (Sonnet 5.5) : ce sont les deux rôles qui se résolvent vers les deux modèles sans claim de revue. Deux sous-agents d'une seule
exécution ne sont pas un échantillon.

Trois valeurs séparées par sujet : **demandé** (le `cacheTtl` lu dans le fichier de profil de ce dépôt, plus le palier, le modèle et l'effort de la
politique), **transmis** (le profil que le hook sélectionne, calculé hors ligne avant le lancement, et le type d'agent que l'hôte a journalisé),
**observé** (tokens d'écriture de cache dans la classe « 1 heure » et dans la classe « 5 minutes », relus dans les journaux de session de l'hôte).
Le lecteur lit, dans la limite de FOUNDRY-ADR-0015, les compteurs (avec leur partage 1 heure / 5 minutes), l'alias de modèle, l'horodatage et
l'identifiant de session (qui sert à trouver le fichier et n'est jamais écrit dans le résultat) ; **au-delà de cette liste**, il lit `type` et
`message.id` de chaque enregistrement (lecteur de `cache_ttl_replay`) et `agentType` des `agent-*.meta.json` (comme l'observateur de PAT-125).
Les sous-agents sont attribués à un sujet par leur **alias de modèle**, jamais par leur contenu.

**Verdict.** `conforming` seulement si le sous-agent Sonnet modifié a écrit du cache dans la classe « 1 heure » et aucun dans la classe
« 5 minutes », **et** si le témoin a écrit dans la classe « 5 minutes » seulement, avec au moins deux requêtes chacun, le bon profil journalisé et le
champ demandé tel qu'attendu. Une classe contraire, un profil divergent, un enfant de plus ou de modèle inattendu, un échec du processus : `not_conforming`.
Tout ce qui n'est pas observé (aucune écriture, une seule requête, journal absent ou illisible, type d'agent absent, version de l'hôte
absente) : `unknown`, jamais conforme. La version de l'hôte est comparée au minimum documenté 2.1.248 et **consignée, non imposée** ; les crédits
d'usage ne sont pas observables par l'outil : `unknown`.

Bornes : une exécution, un parent, deux enfants, aucune relance (l'outil refuse un répertoire de travail ou un fichier de résultat existant : il
bloque un rejeu par chemin, pas une nouvelle exécution avec de nouveaux chemins ; l'autorisation reste celle de l'opérateur).
Le résultat est consigné tel quel. **Si la classe « 1 heure » n'est pas observée, le dernier commit n'est pas retenu, les profils ne sont pas
modifiés et le ticket le dit.** Résultat : **[essai] en attente.**

## Rejeu après coup

`python3 -m foundry.cache_ttl_replay --host-session <journal principal> [...] --subagent-1h-to-5m --until <instant>` (option nouvelle, par défaut
éteinte : toute entrée existante donne la même sortie qu'avant). Un sous-agent dont **toutes** les écritures de cache observées sont dans la classe
« 1 heure » (au moins une) est rejoué vers « 5 minutes » avec la règle déjà écrite pour la conversation principale (sens A, PAT-132 en session ;
aucune règle nouvelle) et forme son propre groupe `subagent_1h`, jamais fusionné avec les sous-agents « 5 minutes » (sens B). Choix par
compteurs seuls, pas par type d'agent (hors ADR-0015) : un sous-agent à « 1 heure » pour une autre raison est rejoué aussi. Limite : le
premier appel d'une lignée qui lit du cache est traité comme celui d'une conversation principale (entrée expirée si aucune lignée principale
antérieure du même alias ne la couvre) ; qui a écrit l'entrée n'est pas observable.

## Règle d'observation et de retour arrière (écrite avant l'adoption)

- **Fenêtre.** Elle commence à la livraison de la version qui porte le changement et se ferme à la première des deux échéances : **les 10
  premières issues livrées** (fusionnées) après cette version, ou **30 jours**. Le mainteneur peut la prolonger par une décision écrite ; sans
  décision, elle n'est pas prolongée.
- **Sessions rejouées, choisies sans regarder le résultat.** À la fermeture de la fenêtre, avant tout calcul : les 5 conversations principales
  les plus récentes (date de modification du fichier) parmi les répertoires de projet de l'hôte qui correspondent à ce dépôt et à ses arbres de
  travail, qui ont au moins un journal de sous-agent et au moins une requête dans la fenêtre. Elles sont figées par `--until` à l'instant de la
  sélection, nommées par rang, jamais par identifiant, et toutes rejouées ; aucune n'est écartée après coup (seul l'outil écarte, en les listant,
  les journaux illisibles ou sans prix). Moins de 3 sessions : **inconnu** (le changement reste, sans conclusion).
- **Quantité lue.** Dans la sortie du rejeu, le groupe `subagent_1h` du modèle `claude-sonnet-5-5` (clé `by_kind_and_model`), lignées tarifables
  seulement, même requêtes des deux côtés : `usd.bounds.<borne>.delta_usd` = coût simulé à 5 minutes moins coût réel à 1 heure, au prix de liste
  (un poids sous abonnement, jamais une facture).
- **Seuil, sur la borne prudente.** Pour l'affirmation « le 1 heure coûte moins », la borne prudente est celle qui donne le **plus petit** écart,
  c'est-à-dire la lecture `favourable` du rejeu (le moins d'expirations simulées à 5 minutes). **Retour à 5 minutes si `delta_usd` de cette borne est
  ≤ 0** (équivalent : `usd.result` différent de `net_loss`, ce nom désignant ici une perte du 5 minutes, donc un gain du 1 heure sur les deux
  bornes). Pas de marge de sécurité ajoutée : aucune ne peut être justifiée par trois sessions.
- **Autres lectures, sans seuil.** Si aucune lignée Sonnet 5.5 de sous-agent n'est observée à « 1 heure » dans la fenêtre, le champ n'a aucun effet
  observable (crédits d'usage, hôte ou précédence : causes **non distinguables** ici) : retour à 5 minutes par simple retrait du champ, sans
  conclusion sur la cause. Si un sous-agent d'un profil non modifié (Opus, etc.) apparaît à « 1 heure », la mesure est **inconnue** (un réglage
  extérieur a changé) et la règle ne conclut pas. Le quota d'abonnement n'est **pas** une entrée de la règle : effet inconnu.
- **Retour arrière exact.** Une PR ordinaire (`foundry:open-pr`) qui retire le bloc `experimental:` / `cacheTtl: 1h` des dix fichiers
  `plugins/foundry/agents/routed-{readonly,worker}-{low,medium,high,xhigh,max}-sonnet-5.5.md`, retire l'insertion correspondante de
  `claude_pin_profile_text` (`plugins/foundry/tooling/foundry/routing_facades.py`) pour que le contrôle déterministe attende de nouveau des
  profils sans champ, retire les tests qui l'affirment et la phrase correspondante de R7 (`AGENTS.md` et `CLAUDE.md`, octet pour octet) et de
  `docs/model-routing.md`. Aucune clé de configuration, aucun réglage utilisateur, aucun routage, modèle ou effort n'est à défaire. Une session
  déjà ouverte garde les profils qu'elle a chargés : les profils d'un plugin sont mis en cache par l'hôte jusqu'au rechargement.

## Hôte antérieur à 2.1.248 et crédits d'usage : documenté, observé, inconnu

| Cas | Documenté [doc] | Observé | Inconnu |
|---|---|---|---|
| Hôte ≥ 2.1.248 | le champ est lu | l'essai tourne sur l'hôte de cette machine (2.1.294) : **[essai] en attente** | le comportement des versions intermédiaires |
| Hôte < 2.1.248 | le champ exige 2.1.248 ou plus | rien (l'essai ne peut observer que l'hôte installé) | si l'hôte ignore le champ, le rejette ou l'accepte |
| Crédits d'usage tirés | `1h` est ignoré | rien (non observable par l'essai ni par les journaux permis) | quand l'abonnement tire des crédits ; l'écriture alors observée est « 5 minutes » |

**Aucune garde n'est inventée** : ni refus de lancement sur un hôte plus ancien, ni détection des crédits d'usage. Dans les deux cas le sous-agent
tourne comme avant (profil sans effet observable), et le rejeu le montre comme une lignée « 5 minutes ».

## Statut documentaire (R5)

- **Option nouvelle de `python3 -m foundry.claude_profile_trial`** : `--cache-ttl-trial` (incompatible avec `--neutral-fixture`) ; schéma de
  résultat `foundry.pat134-cache-ttl-trial.v1` ; constantes publiques `CACHE_TTL_TRIAL_SCHEMA`, `CACHE_TTL_DOCUMENTED_MIN_HOST_VERSION`,
  `CACHE_TTL_POLICY`, `CACHE_TTL_SUBJECTS`, `CACHE_TTL_LOGICAL` et fonctions `profile_cache_ttl`, `cache_ttl_parent_prompt`,
  `observe_cache_ttl`, `cache_ttl_verdict`, `run_cache_ttl` : documentées ici et dans la docstring du module.
- **Option nouvelle de `python3 -m foundry.cache_ttl_replay`** : `--subagent-1h-to-5m` (avec `--host-session` seulement) ; groupe `subagent_1h`,
  clé `subagent_1h_to_5m` (règle et nombre de lignées rejouées, présente avec l'option seulement) ; constantes `SUBAGENT_1H`, `ALL_KINDS`,
  `DIRECTION_A` et fonction `observed_one_hour_only` : documentés ici et dans la docstring.
- **Champ de profil `experimental.cacheTtl`**, règle d'observation et de retour arrière : documentés ici et dans `docs/model-routing.md`.
- **Fichiers non modifiés** : tous les résultats de PAT-132 et de PAT-133, les résultats enregistrés de PAT-125.
- Le détecteur de FOUNDRY-123 n'est pas livré : ce statut est affirmé ici et vérifié en revue, non appliqué mécaniquement.
