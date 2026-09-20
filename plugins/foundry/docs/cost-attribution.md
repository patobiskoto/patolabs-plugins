# Attribution de coût hors ligne

`cost-attribution` relit sur demande un journal JSONL explicitement fourni et applique la grille versionnée locale. Il ne contacte aucun fournisseur et ne modifie ni routage, politique, gate, tracker ni journal hôte. La sortie JSON déterministe ne contient jamais de prompt, réponse, code, chemin, nom de fichier ou autre contenu du journal.

## Compteurs cumulatifs Codex

`total_token_usage` et `thread_token_usage` de Codex sont des compteurs cumulatifs
par session qui peuvent couvrir le même usage avec des bases différentes. Le lecteur
choisit une seule série pour toute la session : `total_token_usage` lorsqu'elle existe,
sinon `thread_token_usage`. Il ne mélange jamais leurs snapshots. Cette série est
autoritaire, y compris si le même événement expose aussi `last_token_usage`. Le lecteur
attribue à chaque événement retenu la différence avec le snapshot précédent, dans
l'ordre du fichier, avant de regrouper par modèle ou par date. Ainsi une session qui
traverse minuit ne recharge pas son préfixe complet dans chaque tranche, et la somme
des tranches conserve exactement l'usage de la session.

Dans ces compteurs natifs, `input_tokens` inclut `cached_input_tokens`. Après avoir
détecté les remises à zéro et calculé chaque différence sur les compteurs bruts, le
lecteur expose le vocabulaire canonique : `input_tokens` contient uniquement l'entrée
non cachée (`input_tokens - cached_input_tokens`) et `cached_input_tokens` reste une
catégorie séparée. Le cache est donc facturé une seule fois. Pour chaque segment, la
somme de l'entrée canonique et du cache retrouve le dernier `input_tokens` natif, et
chaque autre compteur conserve exactement son dernier cumul.

Une baisse d'au moins un compteur dans la série retenue délimite une nouvelle session cumulée dans le fichier :
le nouveau snapshot devient le premier incrément de cette session. Aucun incrément
négatif n'est produit et cette remise à zéro ne bloque pas la lecture. Les journaux
Codex dépourvus de compteur cumulatif conservent leur lecture native par requête. La
sémantique Claude reste également par message et ses compteurs sont déjà des catégories
additives distinctes, sans cette soustraction.

`turn_token_usage` peut cumuler plusieurs requêtes d'un tour : il n'est jamais utilisé
comme une requête. Sans compteur de requête `usage`/`last_token_usage`, le lecteur
s'appuie sur le cumul de session choisi. Si celui-ci manque aussi, il refuse la mesure
comme indisponible, y compris si d'autres événements de la session ont une requête connue.

La cohérence est vérifiée après sélection du flux et calcul de l'incrément. Si le cache
d'un incrément dépasse son entrée inclusive, le lecteur échoue explicitement : il ne
ramène jamais une entrée non cachée négative à zéro. Une évolution du ratio de cache ne
constitue pas à elle seule une remise à zéro, puisque cette décision reste fondée sur
les compteurs natifs bruts.

## Coût par position

Pour les compteurs natifs **par requête**, chaque point de `session_curves` contient uniquement `host`, `session_id`, le `rank` global de la session, modèle canonique, date, palier, coût incrémental et coût cumulé. Le rang reste global quand le modèle ou la date change. L'identité d'une session est le couple `(host, session_id)` : deux hosts qui emploient le même identifiant ne sont jamais fusionnés.

Les journaux qui ne donnent qu'un compteur cumulatif restent tarifés lorsque la grille le permet, mais leur courbe est `position_data: unavailable`. Une absence de rang, un rang dupliqué ou des compteurs partiels produit la même indisponibilité : le lecteur n'invente pas de coût par tour.

Un compteur de dernière requête concurrent doit correspondre exactement à l'incrément
cumulatif retenu, cache compris. S'il diffère, cet incrément conserve son coût total
mais ne prouve aucune position de requête : la courbe devient indisponible, ainsi que
la tarification nécessitant un seuil de contexte par requête.

## Complétude Claude

Certaines lignes `assistant` Claude réelles ne rapportent pas le compteur de raisonnement
(`output_tokens_details.thinking_tokens`). D'autres rapportent un `thinking_tokens`
supérieur à `output_tokens`. Dans les deux cas, les quatre compteurs tarifés restent
valides : la requête contribue donc au coût et à sa position, tandis que son seul
`reasoning_output_tokens` vaut `unavailable`. Le raisonnement reste inclus dans l'output
et n'est jamais tarifé une seconde fois. Un groupe contenant au moins une telle requête
rend son total de raisonnement `unavailable`, jamais un total partiel ou zéro.

La sortie CLI Claude expose `reader_completeness`. Son champ `completeness` décrit
uniquement la ventilation du raisonnement. `billable_records` compte les requêtes
conservées pour la tarification, et `affected_records` donne les comptes content-free par
motif : `missing_reasoning_output_tokens` ou
`incoherent_reasoning_output_tokens`. Les requêtes concernées sont incluses dans
`billable_records`; elles ne figurent pas dans `excluded_records`.

Les lignes dont le modèle vaut exactement `<synthetic>` ne sont pas des appels API. Elles
sont exclues du coût sous le motif `synthetic_model` et ne dégradent pas à elles seules la
complétude du raisonnement des requêtes réelles. Une absence de prix peut toujours rendre
le coût `unavailable`, même lorsque la couverture des compteurs tarifés est complète.

Les compteurs d'entrée, de cache ou de sortie absents, négatifs, booléens ou malformés
restent refusés. Un compteur de raisonnement effectivement présent mais négatif, booléen
ou malformé reste également refusé; seule son incohérence avec l'output bénéficie de la
dégradation typée. Un journal vide ou composé uniquement de lignes `<synthetic>` échoue
explicitement faute de requête tarifable. Un journal dont toutes les requêtes tarifables
omettent la ventilation du raisonnement, lui, rend bien un coût complet avec une
ventilation `unavailable`.

Une répétition d'un même snapshot cumulatif rend aussi la courbe indisponible. Ces notifications ne prouvent pas une nouvelle requête; les totaux historiques restent inchangés, mais l'outil ne prétend pas connaître une position exacte. Les comparaisons qui utilisent une telle session sont donc `unavailable`.

Les tarifs peuvent être fractionnaires en micro-unités. Chaque incrément est la différence entre deux préfixes arrondis de la même série modèle/date, plutôt qu'un arrondi indépendant de requête. Ainsi la somme des points est exactement le `cost_micros` historique de `cost_record`; le cumul global additionne ces incréments sans créer de micro-unité.

Exemple synthétique : deux requêtes à `0.5` micro chacune donnent des points `0`, puis `1`, et un cumul final `1`. Ce résultat est le même que l'arrondi du total observé.

## Comparer des redémarrages observés

Le fichier optionnel `--session-comparisons` déclare une équivalence de travail opaque :

```json
{
  "work-2026-09-a": {
    "long_session": {"host": "codex", "session_id": "long-a"},
    "short_sessions": [
      {"host": "codex", "session_id": "short-a"},
      {"host": "codex", "session_id": "short-b"}
    ]
  }
}
```

```sh
python3 tooling/foundry_cli.py cost-attribution --host codex --log synthetic-long.jsonl \
  --log synthetic-short-a.jsonl --log synthetic-short-b.jsonl \
  --session-comparisons comparisons.json
```

`session_comparisons` rend les coûts réellement observés de la longue session et de la liste ordonnée des courtes sessions, donc les coûts d'amorçage/redémarrage sont inclus. `work_equivalence: caller_declared` signifie que l'appelant atteste un travail égal : l'outil ne le déduit jamais de tokens égaux, ne découpe jamais artificiellement une longue session et n'extrapole aucune exécution.

## Utilisation de plan

Pour Codex, le lecteur reconnaît l'enveloppe native `event_msg` avec `payload.type: token_count`. S'il est valide, il garde seulement `plan_usage_percent` (0 à 100), `window_minutes` et `resets_at`; ce dernier couple est l'identité de la fenêtre. Les fenêtres `primary` et `secondary` restent distinctes, dans leur ordre d'observation. Le scalaire `plan_usage_percent` de session est la dernière observation `primary`, jamais un choix de `secondary` ni une somme ou moyenne. Il rejette booléens, valeurs non finies et valeurs hors plage. Les noms de plan/de limite et les crédits ne sont jamais conservés. Claude rend toujours `plan_usage_percent: unavailable`. L'absence ou la malformation de cette donnée facultative ne retire jamais le coût observé.

Une notification qui ne porte que le quota devient une observation de métadonnée
séparée, avec seulement le couple `(host, session_id)`, son rang, `observed_on`, la
fenêtre et sa provenance. Elle ne crée donc aucune ligne modèle/date, aucune requête à
zéro token et ne réutilise jamais le dernier cumul de tokens. Cela vaut avant la
première requête, après une requête, et après un changement de modèle ou de date. Si le
journal ne contient aucun usage facturable, `plan_usage_by_session` peut rester
disponible tandis que `aggregates` et `session_curves` restent vides.

Les filtres `--from` et `--to` appliquent la date propre `observed_on` à chaque
snapshot de quota. Écarter un snapshot hors fenêtre ne rend pas une courbe de coût
incomplète; inversement, une observation dans la fenêtre reste visible même si aucune
ligne de coût ne l'est. Un champ de compteur explicitement présent mais partiel ou
malformé continue d'échouer fermé. Seule la métadonnée de quota facultative absente ou
malformée est ignorée.
