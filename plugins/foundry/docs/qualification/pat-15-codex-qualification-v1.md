# PAT-15 — rapport de qualification Codex V1

Statut documentaire : ajouté le 3 octobre 2026. Ce rapport importe des observations
natives et résultats mécaniques ; il n'est ni un reçu, ni une attestation, ni une
nouvelle preuve Foundry. Les données structurées sont dans
[le rapport JSON](pat-15-codex-qualification-v1.json).

PAT-ADR-0010 et PAT-14, acceptés, autorisent cette tranche technique urgente Codex.
La comparaison économique et la qualification Claude/cross-host sont différées à
PAT-17. Aucun gain de quota, coût ou capacité utile n'est revendiqué.

| Slot terminé une fois | Modèle et effort demandés/transmis | Identité et effort observés par l'hôte | Résultat mécanique |
|---|---|---|---|
| scout / economy | `gpt-6-luna` / low | `gpt-6-luna` / low | pass, completed |
| implementer / balanced | `gpt-6.1-sol` / medium | `gpt-6.1-sol` / medium | pass, completed |
| reviewer / frontier | `gpt-6.1-sol` / high | `gpt-6.1-sol` / high | pass, revue de qualité mergeable, ancienne AC9 non couverte |
| architect / apex | `gpt-6.1-sol` / max | `gpt-6.1-sol` / max | pass, completed |

Les quatre slots bornés sont terminés, sans retry de qualification. Les observations
proviennent de l'hôte, et non d'une auto-déclaration textuelle du modèle. Le mode
Standard/Fast n'était observable pour aucun slot : valeur `null`, statut
`unavailable`. Aucune comparaison par mode n'est possible. Les digests des fixtures
et résultats disponibles sont conservés dans le JSON. Cette portée est technique
et mécanique, limitée aux tâches bornées ; elle ne démontre pas une qualité générale.

La sélection immuable portait sur la PR #68, base
`7adc33793cc04dfd5102f1e11a5d47cb6ecc9cd0`, head
`edd5b16c088c5f609f07c8eaff8cf4fca447b4f9`, diff SHA-256
`2ebb406139b4e146937bc58b92011725ee8e99a9edf9cae2676eef58a9db9314`.
La preuve canonique de revue normale
`69aba1bfdf2b13d13111ca17a5fe208a577bedfb787ddaa789f34f5b502bd745`
reste historique : qualité mergeable, ancienne AC9 économique `not_covered`.
L'application du report économique déjà accepté actualise le contrat, sans réécrire
cette preuve. Le diff final requiert une nouvelle revue indépendante et sa CI exacte.
La suite ordinaire de livraison ne constitue pas un cinquième slot de qualification.

Les tokens, coût API et consommation isolée du quota restent `null`/indisponibles.
Le snapshot de compte après le slot reviewer indique 19 % hebdomadaires utilisés et
aucun crédit additionnel disponible. Sans baseline préalable, ce snapshot partagé
ne permet ni attribution aux slots, ni différence de consommation, ni gain.
Le lecteur de coût hors ligne confirme un coût `null`/`unavailable` : les modèles
Luna 6 et Sol 6.1 ne sont pas tarifés dans ce lecteur. Il ne fournit aucun comparatif
apparié ni courbe de positions. Aucun identifiant ou chemin de journal natif n'est
exporté. L'autorisation bornée excluait API et crédits additionnels ; aucune
comparaison payante n'a été exécutée.

Le rollback est vérifié en premier par l'exemple
[`codex-gpt-5.6-rollback.json`](../../examples/codex-gpt-5.6-rollback.json) : economy
5.6 Luna/low, balanced 5.6 Terra/medium, frontier 5.6 Sol/high, apex 5.6 Sol/max.
Une comparaison AST confirme exactement le mapping au SHA de référence `7adc337…`
(0.9). `test_codex_rollback.py` passe ses huit checks avant modification des défauts :
priorité des overrides anciens, pins utilisateur, refus des replis non déclarés,
planchers, persistance des escalades, preuves historiques et déduplication de review.
Le même vérificateur passe 13 checks dans le runtime modifié : les huit checks
précédents et cinq refus de fallback automatique vers un compte limité aux anciens modèles. Ce résultat local ne
constitue ni une réinstallation réelle, ni une CI sur un commit de rollback.

Cette révision implémente economy Luna 6/low et balanced/frontier/apex Sol 6.1 aux
efforts medium/high/max. Elle ne reconfigure pas la conversation principale,
préserve Claude et ne promeut pas Astra. La revue indépendante du head final, sa
CI exacte et le merge restent à effectuer par le coordinateur. Les anciennes
preuves ne valent pas validation de ce nouveau diff et PAT-17 reste ouvert.

Validation locale du diff de travail basé sur `edd5b16…` : 653 tests ciblés passent
(1 désélectionné), dont les 13 checks rollback/refus d'anciens modèles disponibles
seuls. Ruff sur les fichiers Python touchés, compilation des deux modules et
`git diff --check` passent. Les commandes sont consignées dans le JSON ; ces
résultats locaux ne constituent pas la CI du futur SHA de livraison.
