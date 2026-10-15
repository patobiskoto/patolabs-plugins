> **ADR** · statut : `accepted`

## Contexte

Rien n'impose aujourd'hui la mise à jour de la documentation. Ni la CI, ni le gate de
merge, ni le contrat du reviewer, ni aucun des seize skills.

La discipline existe pourtant : sur 67 commits touchant `plugins/foundry/tooling`, 44 ont
aussi touché `plugins/foundry/docs`. Le problème n'est pas une absence de culture, c'est
une fuite de 34 % — et cette fuite est caractérisable.

Sur les 23 commits code sans documentation, la majorité n'en avait légitimement pas
besoin : pagination, concision d'un message d'erreur, typage interne. Mais une classe
identifiable aurait dû en produire :

- FOUNDRY-88, `feat` — publication d'événements, receipts et télémétrie vers DevHub ;
- FOUNDRY-91, `feat` — production des receipts d'exécution depuis leurs sources de vérité ;
- FOUNDRY-19, `feat` — diagnostic du routage de modèles dans `doctor` ;
- FOUNDRY-100, `fix` — traduction des modèles Claude aux frontières CLI ;
- FOUNDRY-101, `fix` — préservation de l'identité macOS nécessaire au refresh OAuth.

Trois `feat` livrés sans une ligne. Et les deux `fix` sont ceux qui coûtent le plus cher :
`_CLAUDE_MODEL_IDS`, la table de traduction qui bloquera le prochain modèle Anthropic,
n'est documentée nulle part ; et la contrainte d'exploitation « l'identité OAuth est liée
à la machine », sur laquelle repose la conception du cadenceur de FOUNDRY-ADR-0017, n'est
lisible que dans un titre de commit.

Le biais est net : la fuite corrèle avec le préfixe de commit. Un `feat` appelle la
documentation ; un `fix` semble interne — alors que certains fix changent un contrat
public ou une contrainte d'exploitation.

En amont il manque un référentiel. Le dépôt ne contient aucun fichier de convention. Le
contrat de reviewer de `foundry:maigret` cherche pourtant les écarts « aux conventions du
projet (AGENTS.md/CLAUDE.md) » : il relit contre des fichiers qui n'existent pas, et ce
critère est vide depuis l'origine. Le contrat de processus qui pilote le dépôt vit dans la
configuration utilisateur, hors du dépôt : il n'est ni versionné, ni relu, ni opposable, et
une session Codex ou un clone frais ne l'a pas.

## Décision

**Documenté = jugé documenté.** La documentation suit la sémantique de gate de
FOUNDRY-ADR-0002 : la preuve n'est pas la présence d'un diff dans `docs/`, c'est
l'existence d'un statut explicite sur chaque artefact documenté que le changement touche.

### Le référentiel vit dans le dépôt

Le contrat de processus et la doctrine de documentation sont versionnés dans le dépôt, à
l'emplacement que les hôtes lisent (`CLAUDE.md`, `AGENTS.md`). Ils cessent d'être une
configuration de poste. Le critère « conventions du projet » du reviewer devient non vide
et opposable.

### Un détecteur déterministe, sans verdict

Du code identifie ce qu'un diff touche parmi les artefacts documentés : verbes et options
de CLI, clés de configuration, constantes et vocabulaires publics, contraintes
d'exploitation, symboles cités dans `docs/`, et surfaces décrites par un skill.

Il émet une **liste de candidats**, jamais un jugement. Il ne peut ni bloquer, ni écrire
de documentation, ni décider qu'une documentation est suffisante. Comme le gate CI, il
lit des faits ; l'appréciation revient ailleurs.

### Le jugement en revue, l'obligation en code

La revue statue **par candidat** : documentation mise à jour, ou non nécessaire avec une
raison. Le gate de merge exige que le statut existe pour chaque candidat détecté ; il
n'exige pas que la réponse soit « mise à jour ».

Un diff sans candidat détecté passe sans formalité — c'est le cas majoritaire et il doit
le rester. Un candidat détecté sans statut refuse le merge. Une raison vide, générique ou
non rattachée au candidat ne vaut pas statut.

La dérogation est unique, explicite et tracée dans le verdict, à l'image de
`--allow-no-ci` : elle couvre le cas où le détecteur produit un candidat manifestement
hors sujet. Elle ne peut jamais couvrir un candidat réel laissé sans documentation.

### La validation humaine porte la véracité

Dans une chaîne autonome, la documentation est écrite par le même processus que le code,
comme les tests : le gate mécanique prouve qu'un statut existe, jamais que la
documentation dit vrai. Le verdict humain sur l'Epic (FOUNDRY-ADR-0017) inclut donc que la
documentation reflète ce qui a été construit. C'est le seul point de vérification de
véracité, et il est à sa place : la documentation décrit le besoin autant que la solution.

## Conséquences

Les changements de contrat cessent de passer silencieusement. Le coût par changement reste
faible : la majorité des diffs ne produit aucun candidat, et « non nécessaire » avec une
raison est un statut valide de plein droit.

Le reviewer gagne un critère réel là où il en avait un vide. Le contrat de processus
devient versionné, donc révisable et opposable — y compris pour une session Codex ou un
clone frais.

Le détecteur est faillible dans les deux sens : il peut manquer un artefact documenté et
en signaler un hors sujet. C'est assumé — il produit un signal, pas une garantie, et sa
liste des candidats manqués s'enrichit des cas constatés en revue.

Ne sont couverts ni la qualité rédactionnelle, ni la cohérence globale du corpus, ni la
documentation existante déjà périmée. Cette dernière relève d'un nettoyage distinct : les
documents du sous-système campagne décrivent un chemin gelé depuis le 2026-09-09.

## Alternatives écartées

- **Un gate binaire « `tooling/` modifié ⇒ `docs/` modifié »** — deux tiers des commits
  sans documentation n'en avaient légitimement pas besoin. Ce gate produirait du bruit
  systématique, entraînerait des modifications cosmétiques de documentation pour le
  satisfaire, et finirait contourné.
- **Laisser la revue juger sans détecteur** — c'est l'état actuel : le critère existe dans
  le contrat du reviewer, sans référentiel ni signal, et trois `feat` sont passés.
- **Faire écrire la documentation par le détecteur** — un texte généré depuis un diff
  décrit ce que le code fait, jamais pourquoi ; c'est précisément la partie qui manque
  dans les cas constatés.
- **Exiger une documentation pour tout changement de comportement** — indécidable
  mécaniquement, et revient à un gate binaire déguisé.