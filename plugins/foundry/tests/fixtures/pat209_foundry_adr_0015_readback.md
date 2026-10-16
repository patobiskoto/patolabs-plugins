> **ADR** · statut : `accepted`

## Contexte

FOUNDRY-ADR-0008 (`accepted`) définit la télémétrie passive et lui interdit
explicitement de lire des fichiers : « L'observer ne déclenche aucun appel modèle,
lecture de fichier ou test supplémentaire ». La même ADR exige pourtant dans sa
whitelist les tokens d'entrée, d'entrée cachés, de sortie et de raisonnement, ainsi
qu'un `estimated_cost` portant devise, grille, version, source et complétude.

Ces deux clauses sont incompatibles avec les frontières host réelles.
`docs/model-routing.md` le constate : « The currently supported host boundaries do not
deterministically expose actual token usage \[…\] Those metrics therefore remain
null/unknown rather than being sampled or inferred. » Le hook `PostToolUse` de Claude
ne transmet que le nom du callback ; la capacité post-spawn de Codex ne transmet qu'un
statut. Aucun des deux ne voit la boucle principale, qui représentait 58,9 % du coût
dans le pilote v1.

Conséquence mesurée : le pilote v2 (FOUNDRY-46) a rendu `inconclusive` sur les deux
hosts, tous coûts et toute qualité à `null` — Claude 60 tentatives dont 0 complète,
Codex 48 dont 6. `docs/release-0.8.0-decision.md` interdit toute promotion de politique
tant qu'un gain net cumulatif complet n'est pas démontré. Le programme de routage est
bloqué par l'absence de mesure, pas par l'absence d'hypothèses.

Or les deux hosts écrivent déjà ces compteurs dans leurs propres journaux de session :
`~/.claude/projects/<slug>/<session>.jsonl` porte `message.usage` avec l'input non
caché, le cache read, les deux classes de cache write et l'output ; les rollouts
`~/.codex/sessions/AAAA/MM/JJ/rollout-*.jsonl` portent `token_count.info.total_token_usage`
avec les champs équivalents. Ces champs correspondent colonne pour colonne au tableau
de prix gelé du pilote v1.

## Décision

Foundry est autorisé à lire les journaux de session natifs des hosts, **hors du chemin**
**d'invocation**, comme source d'observation de coût. Cette lecture n'est pas l'observer
d'ADR-0008 et n'en relâche aucune contrainte : elle s'exécute après coup, sur demande
explicite, jamais pendant une invocation, jamais depuis un hook.

L'interdiction de lecture de fichier d'ADR-0008 reste normative **pour l'observer**.
Elle ne s'applique pas à ce lecteur hors-ligne, qui n'a aucun effet sur le routage.

Le lecteur est soumis aux garanties suivantes, non négociables :

* il n'extrait que des compteurs de tokens, un alias de modèle, un horodatage et un
  identifiant de session ; jamais un prompt, une réponse, un extrait de code, un
  chemin de fichier, un nom de fichier, une commande, une sortie de test ou un secret ;
* il n'émet aucune requête réseau et ne modifie aucun journal host ;
* il ne modifie ni routage, ni politique, ni gate, ni état de tracker ; retirer
  entièrement le lecteur ne change aucun comportement de production ;
* il est idempotent : rejouer sur les mêmes journaux rend le même résultat ;
* un rattachement projet impossible reste `unavailable`, jamais deviné.

Le coût est dérivé, jamais déclaré. La grille de prix devient une donnée versionnée
et datée : par entrée `host`, `model`, les cinq colonnes $/Mtok, `source`,
`captured_at`, `effective_from` et `effective_to`. Une session est tarifée avec la
grille en vigueur à sa date, de sorte qu'un recalcul historique reste stable après un
changement de tarif. Deux grilles se chevauchant sur une même paire host/modèle sont
refusées au chargement.

L'identité de modèle devient ouverte et dérivée. `KNOWN_MODELS` cesse d'être une
constante shippée : l'ensemble des modèles connus est dérivé de la grille de prix, via
une table d'alias `nom wire du host -> alias canonique`. Un modèle observé sans entrée
de prix produit un `estimated_cost` `unavailable` **en conservant son alias** ; l'écraser
en `unknown` est interdit. Cela aligne l'implémentation sur la règle déjà normative
d'ADR-0008 : un coût inconnu n'est jamais traité comme zéro.

Le vocabulaire de provenance de l'implémentation est aligné sur celui de l'ADR-0008 :
`client_observed`, `host_reported`, `pricing_derived`, `unavailable`.

## Conséquences

Foundry obtient une attribution de coût réelle, symétrique Claude/Codex, couvrant la
boucle principale, et **rétroactive** sur l'historique de sessions déjà écrit. Le
pilote v3 devient exécutable avec une mesure qui peut conclure.

Un nouveau modèle est routable sans changement de code (`.foundry/model-routing.json`
accepte déjà un nom de modèle libre) et devient mesurable en ajoutant une ligne sourcée
et datée à la grille. Une commande liste les modèles observés sans entrée de prix.

Le contrat s'élargit : format des journaux host, table d'alias, fenêtres de validité
tarifaires. Le format des journaux appartient aux hosts et peut changer sans préavis —
un journal non reconnu doit rendre `unavailable`, jamais une valeur approchée.

## Alternatives écartées

* **Attendre que les hosts exposent l'usage dans les hooks** — c'est la position
  actuelle, elle a produit deux pilotes `inconclusive` et laisse la boucle principale
  invisible par construction.
* **Estimer les tokens par comptage local du contexte** — non observé, non
  reproductible, et contraire à la règle « une estimation nulle plutôt qu'un zéro
  inféré ».
* **Faire remonter le coût uniquement depuis** `total_cost_usd` **du CLI headless** — ne
  couvre que le chemin campagne, ignore la boucle principale interactive, ne permet
  aucun recalcul historique et ne se décompose pas par classe de tokens.
* **Récupérer les tarifs automatiquement depuis les pages fournisseurs** — ADR-0008
  exige `source` et complétude sur chaque estimation ; un scrape silencieux ne fournit
  ni preuve ni date de capture fiable.
* **Superséder ADR-0008** — inutile : sa whitelist exige déjà ces métriques. Seule sa
  clause d'interdiction de lecture de fichier, écrite pour l'observer, avait besoin
  d'être bornée à l'observer.