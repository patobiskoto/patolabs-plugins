# Décision 0.8.0 — aucune promotion runtime (FOUNDRY-47)

## Décision exécutoire

**Décision : `keep`; aucune activation runtime en 0.8.0.** Les mappings, profils,
defaults, providers, permissions, fallbacks, floors de gate, plafond de deux
escalades et la règle « aucun local privilégié » restent exactement ceux de
FOUNDRY-ADR-0006 et FOUNDRY-ADR-0007. Aucun nouveau signal de télémétrie, résultat
d'observation ou succès isolé ne peut modifier le routeur.

Cette décision est une publication de preuve, pas une ADR : elle ne propose aucun
changement de politique. FOUNDRY-ADR-0008 reste normative : une observation n'est
pas une activation et une valeur inconnue n'est jamais zéro.

## Sources et périmètre

- [Rapport F46 v1](../benchmarks/foundry-46/REPORT-v1.md) : matrice historique
  incomplète et verdict `inconclusive`.
- [Rapport F46 v2](../benchmarks/foundry-46/report-v2/pilot-20260825/REPORT-v2.md)
  et ses [résultats typés](../benchmarks/foundry-46/report-v2/pilot-20260825/results-v2.json) :
  matrice F61 v2 complète de 108 coordonnées, mais verdict `inconclusive` par hôte.
- [Protocole F61 v2](../benchmarks/foundry-61/protocol-v2.json) : seuils et
  invariants gelés : métriques inconnues à `null`, coûts cumulés comprenant
  attempts/cache/retries/corrections/escalations, comparaisons seulement dans un
  même hôte, et agrégat inter-hôtes interdit sans pondérations gelées.

Le **7,42 %** de l'ancien rapport de prix est un mélange historique de prix à tokens
et invocations tenus constants. Ce n'est pas une économie nette causale, ne couvre
ni cache, ni retries, ni corrections/escalades, ni allocation de boucle principale,
et ne fonde donc aucune activation.

## Comparaison aux seuils gelés

| Exigence de décision gelée | Claude v2 | Codex v2 | Décision |
|---|---|---|---|
| Matrice exacte et comparaison dans le même hôte/policy | 60 tentatives, 0 complete / 40 failed / 20 unknown | 48 tentatives, 6 complete / 7 failed / 35 unknown | Les compteurs existent, mais ne qualifient pas une promotion. |
| Gain net cumulatif complet (attempts, cache, retries, corrections, escalades) | `null` / unavailable | `null` / unavailable | Seuil non démontré; coût inconnu interdit l'activation. |
| Qualité indépendante : tests avant/après, review, succès aval, régression sécurité | tous `null` / unavailable | tous `null` / unavailable | Seuil non démontré; un terminal hôte, y compris les 6 succès Codex, n'est pas un succès produit. |
| Retries, corrections, escalades | 0 / 0 / 0 observés | 0 / 0 / 0 observés | Observation neutre; elle ne remplace ni coût ni qualité complets. |
| Gates et escalades | floors préservés; plafond de deux respecté | floors préservés; plafond de deux respecté | Préservation requise, mais insuffisante pour promouvoir. |
| Version/attribution runtime | version observée `null`; diagnostic non attribué | version observée `null`; diagnostic non attribué | Contrat runtime non qualifié. |
| Allocation main-loop et agrégat inter-hôtes | allocation `null` | allocation `null`; agrégat `null` sans poids gelés | Aucun gain global ne peut être déclaré. |

Chaque hôte est donc classé **`inconclusive`**, avec disposition de production
**`keep`**. Les diagnostics F46 v2 (40 échecs de processus et 20 schémas Claude;
35 traces invalides et 7 timeouts Codex) sont des observations non attribuées aux
versions attendues, car les versions n'ont pas été observées. Ils ne sont ni effacés
ni convertis en coût, qualité ou causalité.

## Garde-fous de promotion

Une promotion future est interdite tant qu'une **ADR acceptée** n'autorise pas le
changement de politique et qu'une preuve compatible avec le protocole gelé ne montre
pas, pour le périmètre concerné :

1. un gain net cumulatif complet, avec les valeurs indisponibles conservées à `null`;
2. qualité, tests, review, succès aval et absence de régression sécurité observés;
3. floors reviewer `frontier/high` et architect `apex/high` ou plus, sans dégradation
   budgétaire, plus le plafond existant de deux escalades;
4. allocation main-loop/subagent et toute agrégation inter-hôtes justifiées par des
   poids gelés;
5. versions hôtes observées et attribution contractuelle qualifiée.

Ainsi, coût inconnu, seul succès local, gate dégradé, comparaison inter-hôtes sans
poids, ou métrique manquante donnent tous `keep`/`inconclusive`, jamais `activate`.
En l'absence de ces preuves et de l'ADR acceptée, les mappings et profils restent
inchangés; FOUNDRY-47 n'ajoute aucun appel, shadow call, fallback, privilège local ou
activation de runtime.

## Cadre de publication F48

F48 peut publier honnêtement cette décision : la matrice v2 a été observée, ses
limites sont explicites et la production reste protégée. Il ne doit pas annoncer
d'économies, de réduction de coût ou de performance : le gain net et l'allocation de
boucle principale sont `null`, et le 7,42 % historique n'est pas causal.
