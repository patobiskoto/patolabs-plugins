# Organisation Linear par produit

Cette convention est celle de Patolabs. Elle n'impose ni workspace, ni équipe, ni
préfixe aux autres installations Foundry.

## Convention opérationnelle

Patolabs utilise un workspace, une équipe `Pato` et le préfixe partagé `PAT`. Chaque
produit durable a son propre Project Linear : par exemple Foundry, Orfeo ou Romance
Engine. Le Project est l'espace de backlog et d'ADR de ce produit. Une issue de type
Epic reste un chantier parent ; ses issues enfants sont les tâches. Un Epic n'est pas
converti automatiquement en Project.

Les cycles et workflows de l'équipe sont communs. Ils ne déterminent donc pas le
périmètre d'un produit. La navigation s'effectue par le Project, avec des favoris ou
des vues Linear filtrées par Project que chaque personne choisit elle-même. Foundry ne
crée pas de Project, ne déplace pas de ticket, ne change pas le préfixe et ne modifie
aucune préférence Linear pour appliquer cette convention.

## Ce que Foundry sélectionne

Le rattachement dépôt → Project s'appuie sur le remote Git canonique du checkout et sur
les identifiants stables du binding : `canonical_repo`, UUID de Project, UUID d'équipe,
UUID des états et UUID des vocabulaires mappés. Le nom affiché d'une équipe ou d'un
Project, le préfixe `PAT`, `PROJECT_REPO`, un basename de dépôt ou un titre d'issue ne
sont jamais une autorité de sélection. Un nom peut donc changer sans élargir le
contexte d'un dépôt.

Les lectures Linear demandent simultanément l'UUID d'équipe et l'UUID de Project. Les
Documents ADR sont listés par UUID de Project. Une issue ou une relation ADR→issue
retournée hors de ces coordonnées est refusée. Avant une écriture ciblée, le binding du
checkout est résolu à nouveau et chaque endpoint est vérifié : un ticket ou une ADR du
produit B fourni depuis le checkout A échoue avant l'effet provider. Un binding absent,
ambigu ou dont le digest du marqueur dérive échoue aussi sans recherche approximative
dans toute l'équipe.

Les liens transverses restent des liens explicitement identifiés et validés ; ils ne
font jamais entrer le backlog ou les ADR de l'autre Project dans le contexte faisant
autorité du dépôt.

## Recette reproductible

La suite automatisée emploie uniquement le double `LinearWire` : elle ne contacte aucun
workspace Linear. `test_linear_same_team_projects_are_isolated_by_stable_project_ids`
crée deux Projects aux UUID et remotes canoniques distincts dans la même équipe. Ils
partagent volontairement le ticker `LIN` : A contient `LIN-1` et `LIN-2`, B contient
`LIN-3`, et chaque Project contient une ADR `LIN-ADR-0001` avec un titre et un corps
proches. Elle prouve que la recherche du dépôt A n'envoie que ses UUID d'équipe et de
Project, que son backlog et son index ADR n'incluent que ses propres objets, et que les
écritures ciblant le ticket ou l'ADR B sont refusées avant mutation. Les tests de
`test_repository_binding_v1.py` complètent cette preuve avec les bindings absents ou
ambigus, les homonymes et les digests de marqueur.

Cette preuve par double ne constitue pas une recette réelle. Pour en exécuter une, un
opérateur doit fournir deux Projects de test explicitement autorisés dans une même
équipe, leurs UUID et deux dépôts de test aux remotes canoniques distincts. Après avoir
enregistré les bindings sans modifier les produits existants, il doit :

1. créer un ticket et une ADR de test dans chaque Project ;
2. depuis chaque checkout, lister backlog et ADR et conserver les readbacks montrant
   uniquement son Project ;
3. tenter, puis constater le refus avant écriture, d'une référence du Project opposé ;
4. vérifier avec Claude Code et Codex les mêmes bindings et les mêmes refus ;
5. supprimer ou archiver seulement les ressources de test autorisées selon la procédure
   convenue, sans toucher aux tickets de production ni aux ADR de décision.

Cette recette nécessite des écritures Linear nouvelles et une autorisation de ressources
de test. Elle n'est pas exécutée par les tests ni par cette documentation. YouTrack reste
lecture seule et n'est pas une source de repli pour les tickets ou ADR Linear.

## Garanties et limites de PAT-20

PAT-10 a livré le cutover Linear, les ADR project-scoped et une recette sans accès
YouTrack, sur le SHA `f586791` selon sa baseline. PAT-54 (`fbe010c`) a généralisé le binding par dépôt ; PAT-55 (`40cbccc`) a
ensuite livré le grooming et les écritures bornées. PAT-20 ajoute la convention explicite et la
preuve automatisée de deux produits dans la même équipe. Aucun de ces tests ne prouve
que des ressources Linear réelles existent aujourd'hui, ni qu'une session agent Codex
distante a été exécutée : ces points restent à qualifier par la recette autorisée
ci-dessus.
