## Contexte

AGENTS.md#R9 (b) applique PAT-ADR-0018 aux tickets d'un Epic. Pour un ticket sans Epic d'origine, le mainteneur a décidé le 2026-10-10 qu'il n'y a pas de report : toutes les remarques non bloquantes sont corrigées avant le merge, puis relues (PAT-ADR-0022), et la chaîne des parents n'est pas remontée (décision du 2026-10-11). Cette règle n'a pas de critère d'arrêt : chaque correction peut produire de nouvelles remarques. Observé sur la PR #209 (PAT-265) : cinq tours, dont quatre pour des phrases de docstring ; observé aussi par l'audit d'un projet consommateur le 2026-10-19 (D6). Le relecteur « partiel » ne reçoit que le diff `base...HEAD` du vérificateur partagé ; la plage depuis le tour précédent est une déclaration du coordinateur (limite documentée de PAT-ADR-0022).

## Décision

Un ticket sans Epic d'origine suit la même règle qu'un ticket d'Epic : après un tour validé (tous les critères `pass`, qualité `mergeable`), ses remarques non bloquantes sont reportées, sauf les trois cas toujours corrigés avant merge (remarque marquée « fix before merge », page gelée après publication, statut de documentation R5 manquant ou générique).

Faute d'Epic d'origine, la destination est un Epic compagnon par projet, nommé exactement `Nits` (un dépôt n'est lié qu'à un projet, FOUNDRY-ADR-0026), avec les mêmes règles que PAT-ADR-0018 : aucun lien de tracker, un seul ticket de suite ouvert qui regroupe les remarques, un nouveau ticket si celui-ci est démarré ou clos, et les quatre écritures autorisées d'avance de PAT-ADR-0018 étendues à cet Epic compagnon. Il n'y a toujours pas de report au second degré : les remarques des PR des tickets d'un Epic compagnon, y compris `Nits`, sont corrigées avant merge.

Le paquet de relecture partielle donne en plus au relecteur le diff de la plage depuis le tour précédent, lu par le vérificateur partagé, pour que la relecture partielle porte sur les octets et non sur une déclaration.

Cette décision amende la décision du mainteneur du 2026-10-10 (pas de report sans Epic d'origine) et étend PAT-ADR-0018 ; elle ne change ni la porte de fusion, ni le format de la preuve, ni PAT-ADR-0022.

## Conséquences

Un ticket sans Epic se clôt en un tour validé plus, au besoin, un tour pour les trois cas toujours corrigés. Les remarques restent tracées dans `Nits`. Le code qui reconnaît un Epic compagnon (`campaign_defer._COMPANION_NAME`) doit aussi reconnaître le titre exact `Nits`. Risque accepté : des remarques s'accumulent dans un Epic compagnon que personne ne priorise ; le traiter reste un verdict humain (FOUNDRY-ADR-0014).

## Alternatives écartées

- Limiter à N tours puis reporter : seuil arbitraire, et un tour de plus reste coûteux.
- Garder la règle actuelle : pas de critère d'arrêt, observé coûteux.
- Rattacher le ticket de suite au ticket d'origine : un ticket clos ne reçoit pas de remarques (décision du 2026-10-11).
