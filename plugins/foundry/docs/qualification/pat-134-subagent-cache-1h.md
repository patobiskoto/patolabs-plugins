# PAT-134 — Cache de prompt à « 1 heure » pour les sous-agents Sonnet 5.5 : essai natif, règle d'observation et retour arrière

PAT-134. Cadre : FOUNDRY-ADR-0015 (lecteur hors ligne des journaux de session de l'hôte : compteurs de tokens, alias de modèle, horodatage,
identifiant de session ; champs au-delà nommés), FOUNDRY-ADR-0008 (modèle, effort et politique de contexte découplés), FOUNDRY-ADR-0019 (par
analogie : une configuration supposée moins chère s'adopte sous observation, avec retour arrière), AGENTS.md R5, R6, R7. Suite de
[`pat-133-cache-ttl-interactive-v1.md`](pat-133-cache-ttl-interactive-v1.md) (dont le fichier d'agrégats et ceux de PAT-132 ne sont **pas
modifiés**). Décision du mainteneur (2026-10-10) : « Ok go et on enchaine » sur la proposition de passer à « 1 heure » les seuls profils
d'agents Sonnet 5.5, adoptée sous observation, sans banc ; un essai natif borné est couvert par son accord antérieur pour un banc.

Légende. **[doc]** : fait de la documentation d'Anthropic **lu par le coordinateur le 2026-10-09** (`code.claude.com/docs/en/sub-agents` et
`prompt-caching`), **non revérifié par l'outillage**. **[code]** : lu dans le dépôt. **[essai]** : à lire dans le fichier de résultat de l'essai
natif consigné dans [`pat-134-native-trial.json`](pat-134-native-trial.json) (voir plus bas).

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
- Le changement est porté par un commit séparé des outils et de cette page (écartable tant que l'essai natif n'avait pas été consigné). Le contrôle déterministe des profils préchargés (`claude_pin_profile_text`, comparé octet pour octet par
  `claude_invocation_binding`) attend exactement ce champ pour ces dix profils et rejette tout profil divergent : champ manquant sur un profil
  Sonnet 5.5, champ sur tout autre profil, autre valeur, autre clé sous `experimental` (`tests/test_claude_profiles.py`). **Portée exacte** : ce
  refus ne vaut que lorsque la route rend un profil épinglé (modèle versionné). Sur une route par alias court (`sonnet`, `opus`…), aucun fichier de
  profil n'est lu (comportement inchangé) ; un modèle de projet traduit vers un alias court suit le même chemin.
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
`message.id` de chaque enregistrement (lecteur de `cache_ttl_replay`), `agentType` des `agent-*.meta.json` (comme l'observateur de PAT-125) et
l'empreinte sha256 de chaque journal d'enfant (`log_sha256`, un condensat du fichier entier, pour l'identifier sans le conserver).
**Écart déclaré, que le mainteneur doit reconnaître** : `log_sha256` est le condensat d'un journal entier de l'hôte, et il est versé dans le
résultat. Il est hors de la liste de FOUNDRY-ADR-0015 (compteurs, alias de modèle, horodatage, identifiant de session) et proche du « digest de
contenu » que FOUNDRY-ADR-0008 exclut de la télémétrie. Le fichier de résultat est une pièce de qualification versée, non de la télémétrie sous
`FOUNDRY_DATA`, et les résultats déjà versés de PAT-125 et de PAT-16 portent la même clé : c'est un précédent, pas une autorisation. Le
résultat consigné n'est pas modifié ; accepter cet écart ou retirer la clé de l'outil pour un essai futur est une décision du mainteneur, non
prise ici.
Une requête dont les classes d'écriture ne totalisent pas le total (ambiguë) rend la classe de cache de son sujet `unknown` : elle pourrait
cacher une écriture de l'autre classe.
Les sous-agents sont attribués à un sujet par leur **alias de modèle**, jamais par leur contenu.

**Verdict.** `conforming` seulement si le sous-agent Sonnet modifié a écrit du cache dans la classe « 1 heure » et aucun dans la classe
« 5 minutes », **et** si le témoin a écrit dans la classe « 5 minutes » seulement, avec au moins deux requêtes chacun, le bon profil journalisé et le
champ demandé tel qu'attendu. Une classe contraire, un profil divergent, un enfant de plus ou de modèle inattendu, un échec du processus : `not_conforming`.
Tout ce qui n'est pas observé (aucune écriture, une seule requête, journal absent ou illisible, type d'agent absent, version de l'hôte
absente) : `unknown`, jamais conforme. La version de l'hôte est comparée au minimum documenté 2.1.248 et **consignée, non imposée** ; les crédits
d'usage ne sont pas observables par l'outil : `unknown`.

Bornes : une exécution, un parent, deux enfants, aucune relance (l'outil refuse un répertoire de travail ou un fichier de résultat existant : il
bloque un rejeu par chemin, pas une nouvelle exécution avec de nouveaux chemins ; l'autorisation reste celle de l'opérateur).
Le résultat est consigné tel quel. **Si la classe « 1 heure » n'est pas observée, le commit qui porte le champ n'est pas retenu, les profils ne sont pas
modifiés et le ticket le dit.** (L'essai du 2026-10-10 a observé cette classe : voir le résultat.)

### Résultat de l'essai (2026-10-10, une exécution, consigné tel quel)

Fichier : [`pat-134-native-trial.json`](pat-134-native-trial.json), écrit par l'outil et versé sans modification (octet pour octet), lancé sur
le commit `50c83f4` d'un arbre propre (`source.dirty` = false), hôte Claude Code 2.1.294, 20,4 s, sortie 0. **Verdict `conforming`**,
`not_established` vide.

| Sujet | Profil journalisé | Requêtes | Écrit « 1 heure » | Écrit « 5 minutes » | Lu |
|---|---|---|---|---|---|
| modifié (Sonnet 5.5, lecture seule) | `routed-readonly-low-sonnet-5.5` | 3 | 4 768 | 0 | 9 103 |
| témoin (Opus 5.5, exécution) | `routed-worker-medium-opus-5.5` | 3 | 0 | 5 301 | 10 242 |

Demandé (`cacheTtl` du profil) : `1h` pour le sujet modifié, absent pour le témoin ; transmis : les deux profils attendus ; observé : le
tableau ci-dessus. **Ce qu'une exécution de deux sous-agents n'établit pas** : que le champ soit honoré pour les neuf autres profils Sonnet 5.5
(un seul a été lancé : lecture seule, effort `low`) ni pour des sessions longues ; que l'effet tienne sur d'autres versions de l'hôte ; que
l'effet soit le même sous crédits d'usage (inconnu) ; un coût, un gain ou un effet sur le quota (inconnus) ; une régularité (deux
sous-agents, une exécution, pas un échantillon ; le témoin est un profil d'exécution, non de lecture seule). Les empreintes des journaux
sont dans le fichier ; les journaux eux-mêmes restent hors dépôt.

### Incident de l'essai, déclaré

Avant l'exécution réelle, le coordinateur a lancé `--dry-run`. À ce moment l'outil créait quand même le répertoire de travail (fichiers du
fixture) avant de s'arrêter ; la première invocation réelle sur ce chemin a donc été **refusée avant tout lancement** (« the work directory and
the result file must not exist »). **Aucun appel infonuagique n'a été fait par cette invocation refusée.** L'essai a ensuite tourné **une seule
fois**, avec un nouveau répertoire de travail. Correction (commit de cette section) : `--dry-run` ne crée plus rien sous le répertoire de travail (la politique
du fixture est construite dans un répertoire temporaire du système, qui existe brièvement puis est supprimé), dans le mode de PAT-134 **et** dans le mode par défaut de PAT-125, qui
avait le même défaut ; tests dans `tests/test_cache_ttl_trial.py` et `tests/test_claude_haiku55.py`. Rien d'autre ne change dans le mode
par défaut de PAT-125 ; ses résultats enregistrés sont intacts.

## Rejeu après coup

`python3 -m foundry.cache_ttl_replay --host-session <journal principal> [...] --subagent-1h-to-5m --until <instant>` (option nouvelle, par défaut
éteinte : toute entrée existante donne la même sortie qu'avant). Un sous-agent dont **toutes** les écritures de cache observées sont dans la classe
« 1 heure » (au moins une) est rejoué vers « 5 minutes » avec la règle déjà écrite pour la conversation principale (sens A, PAT-132 en session ;
aucune règle nouvelle) et forme son propre groupe `subagent_1h`, jamais fusionné avec les sous-agents « 5 minutes » (sens B). Choix par
compteurs seuls, pas par type d'agent (hors ADR-0015) : un sous-agent à « 1 heure » pour une autre raison est rejoué aussi. Limite : le
premier appel d'une lignée qui lit du cache est traité comme celui d'une conversation principale (entrée expirée si aucune lignée principale
antérieure du même alias ne la couvre) ; qui a écrit l'entrée n'est pas observable. **Conséquence** : avec une conversation principale d'un autre
modèle (le cas typique de Foundry : Opus en principal, Sonnet en sous-agents), aucune lignée principale du même alias n'existe, et cette entrée
est comptée expirée sous les **deux** bornes ; sa lecture est alors retarifée comme une écriture à 5 minutes, bien qu'un préfixe écrit par un
sous-agent frère moins de 5 minutes plus tôt aurait aussi été lu à 5 minutes. **Les écarts `usd.bounds.*.delta_usd` et `usd.result` surestiment donc
le coût du 5 minutes sur les deux bornes.** La règle de rejeu n'est pas modifiée ; la décision de retour arrière (ci-dessous) lit une quantité
supplémentaire qui n'a pas ce biais.

## Règle d'observation et de retour arrière (écrite avant l'adoption)

- **Fenêtre.** Elle commence à la livraison de la version qui porte le changement et se ferme à la première des deux échéances : **les 10
  premières issues livrées** (fusionnées) après cette version, ou **30 jours**. Le mainteneur peut la prolonger par une décision écrite ; sans
  décision, elle n'est pas prolongée.
  Début retenu, sans changement de cette règle : la fusion de la pull request de release de la 1.1.0 (distribution effective, dérivée, non
  observée). Le mainteneur a décidé le 2026-10-10 que la fenêtre commence à la publication de la 1.1.0 ; lire « publication » comme cette
  fusion était la lecture du coordinateur, que le mainteneur a confirmée le 2026-10-10, avant la fusion. Voir [`release-1.1.0.md`](../release-1.1.0.md),
  « Observation windows this version carries ».
- **Sessions rejouées, choisies sans regarder le résultat.** À la fermeture de la fenêtre, avant tout calcul : les 5 conversations principales
  les plus récentes (date de modification du fichier) parmi les répertoires de projet de l'hôte qui correspondent à ce dépôt et à ses arbres de
  travail, qui ont au moins un journal de sous-agent et au moins une requête dans la fenêtre. Elles sont figées par `--until` à l'instant de la
  sélection, nommées par rang, jamais par identifiant, et toutes rejouées ; aucune n'est écartée après coup (seul l'outil écarte, en les listant,
  les journaux illisibles ou sans prix). Moins de 3 sessions : **inconnu** (le changement reste, sans conclusion).
- **Quantité lue.** Dans la sortie du rejeu, le groupe `subagent_1h` du modèle `claude-sonnet-5-5` (clé `by_kind_and_model`), lignées tarifables
  seulement, mêmes requêtes des deux côtés, au prix de liste (un poids sous abonnement, jamais une facture). Un écart est **le coût simulé à
  5 minutes moins le coût réel à 1 heure** : **positif veut dire que le 1 heure a coûté moins** ; négatif ou nul, qu'il n'a pas coûté moins.
  La décision **ne lit pas** `usd.bounds.*.delta_usd` ni `usd.result` (biais du premier appel, ci-dessus) mais
  `usd.entry_reads_not_expired.delta_usd_exact`, ajout à la sortie (pas une règle de rejeu) : la même simulation, avec la lecture d'entrée du
  premier appel de chaque lignée comptée **non expirée**, en chaînes décimales exactes (non arrondies). Cette lecture est **plus sévère que
  `delta_usd`** pour le maintien du 1 heure (elle ne lui est jamais supérieure) ; elle n'est **pas** la plus sévère dans l'absolu (limites de
  la décision, plus bas).
- **Seuil.** Des deux bornes de cette quantité, la décision prend la **plus petite** : en pratique la lecture `favourable`, qui simule le
  moins d'expirations, donc le coût simulé à 5 minutes le plus bas et l'écart le plus petit. La lecture `prudent` simule plus d'expirations,
  donne l'écart le plus grand et est la borne **la plus favorable** au maintien du 1 heure : le seuil **n'est pas** posé sur la borne `prudent`
  (le minimum est pris pour ne pas dépendre de cet ordre). **On garde le 1 heure seulement si ce minimum est strictement
  positif ; le retour à 5 minutes est déclenché quand, sur cette lecture, le 1 heure n'est pas moins cher (minimum ≤ 0).** Rapport avec
  `usd.result` : comme la quantité lue ne dépasse jamais `delta_usd`, `keep` implique `net_loss`, **mais `net_loss` n'implique pas `keep`** (le
  premier appel peut, à lui seul, faire basculer le signe : c'est le cas testé). La quantité est exacte, sans l'arrondi à six décimales de
  `delta_usd` : un écart positif qui s'arrondirait à 0 compte comme positif. Pas de marge de sécurité ajoutée : aucune ne peut être justifiée
  par trois sessions. La règle est implémentée par `rollback_decision` (`cache_ttl_replay.py`), qui rend `keep`, `roll_back` ou `unknown`.
- **Limites de la décision : deux biais vers `keep` subsistent, sous les deux bornes.** (a) Quand une expiration est comptée à l'intérieur
  d'une lignée (écart de plus de 300 s), **toute** la lecture est retarifée comme une écriture à 5 minutes, alors que la partie commune aux
  sous-agents d'un même profil (outils et prompt système), si un sous-agent frère de ce profil était actif moins de 5 minutes plus tôt, serait
  restée lisible à 5 minutes : le coût simulé à 5 minutes est surestimé. (b) L'écart `favourable` n'est pas une borne inférieure rigoureuse de
  l'écart réel (l'instant d'envoi d'une requête n'est pas observable) : une expiration qui n'a pas eu lieu peut être comptée, sous les deux
  bornes. Le seuil étant sans marge (strictement positif), l'un ou l'autre biais peut faire passer un cas marginal de `roll_back` à `keep` :
  **un `keep` sur un écart proche de zéro n'est pas établi par cette règle.** Aucun des deux n'est corrigé ici ; la règle n'est pas modifiée.
- **Appel de la décision (pas de commande dédiée).** Sur la sortie JSON d'un rejeu fait avec `--subagent-1h-to-5m` et `--out` :
  `PYTHONPATH=plugins/foundry/tooling python3 -c 'import json, sys; from foundry.cache_ttl_replay import rollback_decision; print(json.dumps(rollback_decision(json.load(open(sys.argv[1], encoding="utf-8")))))' <sortie du rejeu>.json`.
  Clés rendues : `decision` (`keep`, `roll_back`, `unknown`) et `reason` ; `contributing_sessions` (nombre de sessions qui contribuent) avec les
  deux derniers codes ci-dessous ; `delta_usd_entry_reads_not_expired` (le minimum lu, chaîne décimale exacte) avec le dernier seulement. Codes
  de `reason`, dans l'ordre où ils sont testés : `no_model_carries_the_field` (`unknown`), `replay_not_made_with_subagent_1h_to_5m`
  (`unknown`), `fewer_designated_sessions_than_the_minimum` (`unknown`), `a_lineage_of_another_model_is_observed_at_1h` (`unknown`),
  `no_lineage_of_the_model_observed_at_1h` (`roll_back`), `a_lineage_observed_at_1h_is_not_priced` (`unknown`),
  `fewer_contributing_sessions_than_the_minimum` (`unknown`), `least_favourable_entry_reads_not_expired_delta` (`keep` ou `roll_back`).
- **Quand la décision est `unknown`** (jamais `keep`) : aucun modèle ne porte le champ (`CLAUDE_CACHE_TTL_1H_PINS` vide, donc après un retour
  arrière : il n'y a plus rien à décider) ; rejeu fait sans l'option ; moins de 3 sessions **désignées** ; moins de 3 sessions qui
  **contribuent** au moins une lignée Sonnet 5.5 de sous-agent tarifable observée à 1 heure ; **une** lignée Sonnet 5.5 de sous-agent observée à
  1 heure non tarifable ; une lignée d'un **autre** modèle (profil non modifié) observée à 1 heure (un réglage extérieur a changé la mesure).
  Seules comptent les lignées dont **toutes** les écritures sont à 1 heure ; une lignée mixte reste en sens B. Limite : un sous-agent hors Foundry
  du même modèle à 1 heure par un réglage extérieur ne se distingue pas ici.
- **Autres lectures.** Si aucune lignée Sonnet 5.5 de sous-agent n'est observée à « 1 heure » dans la fenêtre (`rollback_decision` : `roll_back`), le champ n'a aucun effet
  observable (crédits d'usage, hôte ou précédence : causes **non distinguables** ici) : retour à 5 minutes par simple retrait du champ, sans
  conclusion sur la cause. Si un sous-agent d'un profil non modifié (Opus, etc.) apparaît à « 1 heure », la mesure est **inconnue** (`rollback_decision` : `unknown`). Le quota d'abonnement n'est **pas** une entrée de la règle : effet inconnu.
- **Asymétrie assumée.** Zéro lignée Sonnet 5.5 à « 1 heure » donne `roll_back`, **y compris quand les sessions n'ont lancé aucun sous-agent
  Sonnet 5.5** (rien n'était observable), alors qu'une ou deux sessions contributrices donnent `unknown`. `rollback_decision` ne distingue pas
  « des sous-agents Sonnet 5.5 ont tourné à 5 minutes » de « aucun n'a tourné » : même code `no_lineage_of_the_model_observed_at_1h`. Une
  fenêtre sans sous-agent Sonnet 5.5 déclenche donc le retour arrière **sans rien apprendre sur le coût**. C'est voulu : ce retour ne défait
  aucun effet mesuré, il retire un champ expérimental dont le risque sur un hôte ancien est inconnu et que rien, sur la fenêtre, ne justifie de
  garder ; `unknown` l'aurait laissé en place sans échéance. La règle écrite avant l'adoption n'est pas modifiée.
- **Retour arrière exact (une seule procédure, quatre étapes).** Une PR ordinaire (`foundry:open-pr`) qui :
  1. écrit `CLAUDE_CACHE_TTL_1H_PINS = ()` dans `plugins/foundry/tooling/foundry/routing_facades.py` (et met à jour le commentaire qui
     précède). Aucune autre ligne de code ne change : `claude_pin_profile_text` ne rend alors plus le bloc, `CLAUDE_CACHE_TTL_1H_MODELS` devient
     `()`, `ROLLBACK_MODEL` (`cache_ttl_replay.py`) et `CACHE_TTL_POLICY` (`claude_profile_trial.py`) deviennent `None` ;
  2. régénère les profils par `python3 plugins/foundry/tooling/generate_claude_profiles.py`, ce qui retire le bloc `experimental:` /
     `cacheTtl: 1h` des dix fichiers `plugins/foundry/agents/routed-{readonly,worker}-{low,medium,high,xhigh,max}-sonnet-5.5.md` et d'eux seuls ;
  3. modifie **trois tests, et aucun autre** (ce sont les seuls qui échouent après les étapes 1 et 2 : 6 cas) :
     `tests/test_claude_profiles.py::test_exactly_the_ten_sonnet_55_pins_carry_experimental_cache_ttl_1h_as_a_nested_map` (attendre l'ensemble
     vide, `carrying == set()`, et retirer `assert len(carrying) == 10` ; le renommer) ;
     `tests/test_claude_profiles.py::test_a_divergent_sonnet_55_profile_is_refused_before_launch` (à supprimer : ses quatre mutations portent sur
     un bloc qui n'existe plus) ;
     `tests/test_cache_ttl_trial.py::test_the_shipped_checkout_requests_1h_for_the_modified_profile_and_nothing_for_the_control` (attendre `None`
     pour le profil modifié aussi). Les cas de `tests/test_cache_ttl_trial.py` qui exécutent le mode d'essai (23 à ce jour) **se sautent
     d'eux-mêmes** (`skipped`), puisque le mode refuse ; les tests de la décision (`tests/test_cache_ttl_interactive.py`) nomment le modèle et
     passent tels quels, comme tous les autres ;
  4. met à jour les phrases qui affirment le champ : la puce de R7 (`AGENTS.md` et `CLAUDE.md`, octet pour octet), la section PAT-134 de
     `docs/model-routing.md`, une entrée de `CHANGELOG.md`, et cette page (date du retour, décision lue).

  **Ce qui reste en état de marche après le retour** : `python3 -m foundry.cache_ttl_replay` (modes de PAT-132 et PAT-133, et
  `--subagent-1h-to-5m`), le mode par défaut de `foundry.claude_profile_trial` (PAT-125). `--cache-ttl-trial` refuse (code de sortie 2,
  « no pin carries experimental.cacheTtl », rien de créé sous le répertoire de travail) et `rollback_decision`, appelée sans modèle, rend
  `unknown` / `no_model_carries_the_field`. Aucune clé de configuration, aucun réglage utilisateur, aucun routage, modèle ou effort n'est à
  défaire. Une session déjà ouverte garde les profils qu'elle a chargés : les profils d'un plugin sont mis en cache par l'hôte jusqu'au
  rechargement.

  **Exécution à blanc (2026-10-10, sur une copie du dépôt, rien n'est versé de cette copie).** Avant correction, l'étape 1 seule faisait
  échouer l'import de `cache_ttl_replay` et de `claude_profile_trial` (`IndexError`) : les deux commandes ne démarraient plus et quatre modules
  de tests n'étaient plus collectés (`test_cache_ttl_interactive.py`, `test_cache_ttl_replay.py`, `test_cache_ttl_trial.py`,
  `test_claude_haiku55.py`). Après correction : étapes 1 et 2, puis les commandes de la CI (`.github/workflows/ci.yml`) : compilation et
  `ruff` passent, `tests/test_routing_contract.py` et la suite de conformité passent, et la suite principale échoue sur les 6 cas des trois
  tests de l'étape 3, et sur eux seuls ; après l'étape 3 : 6 296 réussis, 23 sautés (ceux du mode d'essai), 0 échec. Les deux contrôles du catalogue
  (`scripts/test_validate_plugins.py`, `scripts/validate_plugins.py`) passent. L'étape 4 (phrases) n'a pas été jouée : aucun test n'affirme les
  phrases de PAT-134 (ceux qui lisent ces fichiers vérifient d'autres règles et l'identité de `AGENTS.md` et `CLAUDE.md`). `tests/test_cache_ttl_trial.py` rejoue les étapes 1 et 2 sur une copie à chaque
  exécution de la suite et importe les deux modules dans un interpréteur neuf.

## Hôte antérieur à 2.1.248 et crédits d'usage : documenté, observé, inconnu

| Cas | Documenté [doc] | Observé | Inconnu |
|---|---|---|---|
| Hôte ≥ 2.1.248 | le champ est lu | **sur l'hôte 2.1.294, le champ est honoré pour un sous-agent Sonnet 5.5 en lecture seule** (1 heure seulement, essai du 2026-10-10) | le comportement des versions intermédiaires et des neuf autres profils Sonnet 5.5 |
| Hôte < 2.1.248 | le champ exige 2.1.248 ou plus | rien (l'essai ne peut observer que l'hôte installé) | si l'hôte ignore le champ, le rejette ou l'accepte |
| Crédits d'usage tirés | `1h` est ignoré | rien : l'état des crédits pendant l'essai est inconnu (non observable par l'essai ni par les journaux permis) | quand l'abonnement tire des crédits ; la classe réellement écrite dans ce cas (« 5 minutes » est attendu d'après la documentation, jamais observé) |

**Aucune garde n'est inventée** : ni refus de lancement sur un hôte plus ancien, ni détection des crédits d'usage. Ce qui est **attendu, non
observé** : sous crédits d'usage, ou sur un hôte qui ignore le champ, le sous-agent tournerait comme avant et le rejeu le montrerait comme une
lignée « 5 minutes ». **Risque, dit sans détour** : si un hôte antérieur à 2.1.248 **rejetait** un profil portant un champ inconnu, **chaque
lancement d'un sous-agent Sonnet 5.5 de Foundry échouerait** sur cet hôte, et Sonnet 5.5 n'a aucun minimum d'hôte dans
`CLAUDE_MODEL_MIN_HOST_VERSION` (seul `haiku-5.5` en a un). Ce comportement est **inconnu** (non documenté pour ce cas, non observé) ; la
documentation lue ne dit pas qu'il est sûr. Retour arrière : la procédure ci-dessus.

## Corrections de la seconde revue (2026-10-10, déclarées)

- La procédure de retour arrière écrite jusque-là était incomplète et cassait deux modules à l'import (voir « Exécution à blanc ») : corrigé
  dans le code, testé, et la procédure réécrite d'après son exécution réelle sur une copie.
- L'énoncé du seuil était faux à deux endroits (`docs/model-routing.md` et `CHANGELOG.md` disaient « sur la borne prudente ») et trop fort
  ici (« la lecture la plus sévère ») : corrigé ; deux biais vers `keep` sont maintenant nommés comme limites de la décision. **La règle de
  décision elle-même n'a pas changé.**
- `cache_ttl_verdict` testait un état `below_minimum` que rien ne produisait : retiré (la version de l'hôte est consignée, jamais un motif
  d'échec) ; le résultat consigné se vérifie toujours `conforming` avec la règle de l'outil.
- Ajouts d'énoncé seulement : asymétrie assumée du `roll_back` sans lignée, appel et codes de `rollback_decision`, écart `log_sha256`,
  risque nommé dans R7.

## Statut documentaire (R5)

- **Option nouvelle de `python3 -m foundry.claude_profile_trial`** : `--cache-ttl-trial` (incompatible avec `--neutral-fixture`) ; schéma de
  résultat `foundry.pat134-cache-ttl-trial.v1` ; constantes publiques `CACHE_TTL_TRIAL_SCHEMA`, `CACHE_TTL_DOCUMENTED_MIN_HOST_VERSION`,
  `CACHE_TTL_POLICY` (`None` quand aucun pin ne porte le champ : le mode refuse alors, code de sortie 2), `CACHE_TTL_SUBJECTS`, `CACHE_TTL_LOGICAL` et fonctions `profile_cache_ttl`, `cache_ttl_parent_prompt`,
  `observe_cache_ttl`, `cache_ttl_verdict`, `run_cache_ttl` : documentées ici et dans la docstring du module.
- **Option nouvelle de `python3 -m foundry.cache_ttl_replay`** : `--subagent-1h-to-5m` (avec `--host-session` seulement) ; groupe `subagent_1h`,
  clé `subagent_1h_to_5m` (règle et nombre de lignées rejouées, présente avec l'option seulement) ; constantes `SUBAGENT_1H`, `ALL_KINDS`,
  `DIRECTION_A`, `ROLLBACK_MIN_SESSIONS`, `ROLLBACK_MODEL` (`None` quand aucun pin ne porte le champ) et fonctions `observed_one_hour_only`,
  `rollback_decision` (appel, clés rendues et codes de `reason` : « Appel de la décision » ci-dessus) : documentés ici et dans la docstring.
- **Champ de profil `experimental.cacheTtl`**, règle d'observation et de retour arrière : documentés ici et dans `docs/model-routing.md`.
- **`--dry-run` des deux modes de `claude_profile_trial`** : ne crée plus rien sous le répertoire de travail (incident ci-dessus) ; ni un
  refus (liaison de profil absente ou divergente, aucun pin porteur du champ) : le répertoire de travail n'est créé qu'après tous les refus. Un
  répertoire temporaire du système existe brièvement pendant la résolution de la politique, puis est supprimé.
- **`usd.entry_reads_not_expired`** (groupe `subagent_1h` seulement : `delta_usd`, `delta_usd_exact`, `first_requests_priced`) et
  `CLAUDE_CACHE_TTL_1H_MODELS` (`routing_facades.py`) : documentés ici.
- **Code de `reason` nouveau** `no_model_carries_the_field` et **refus nouveau** de `--cache-ttl-trial` quand aucun pin ne porte le champ :
  documentés ici (règle, appel de la décision, retour arrière) et dans `docs/model-routing.md`.
- **Nouveau fichier** : `pat-134-native-trial.json`, résultat de l'essai, versé tel quel.
- **Fichiers non modifiés** : tous les résultats de PAT-132 et de PAT-133, les résultats enregistrés de PAT-125.
- Le détecteur de FOUNDRY-123 n'est pas livré : ce statut est affirmé ici et vérifié en revue, non appliqué mécaniquement.
