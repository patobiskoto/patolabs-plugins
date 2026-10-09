# PAT-19 — Rejeu hors ligne du cache de prompt « 1 heure » contre un cache simulé « 5 minutes » (PAT-132)

PAT-132. Cadre : FOUNDRY-ADR-0015 (lecteur hors ligne des journaux de session de l'hôte : seuls des compteurs de tokens, un alias de
modèle, un horodatage et un identifiant de session ; coût dérivé ; grille de prix versionnée), FOUNDRY-ADR-0008, AGENTS.md R5 et R6.
Suite de [`pat-19-cost-breakdown-v1.md`](pat-19-cost-breakdown-v1.md) (PAT-129), qui a mesuré que l'écriture de cache pèse 56 à 59 % du
coût pondéré et que les 104 sessions cloud écrivent toutes à la durée « 1 heure ». **Aucune règle, aucun protocole, aucune
configuration, aucun résultat ni verdict gelé n'est modifié ou requalifié. Aucun réglage du lanceur ni du routage n'est modifié.** Rien
n'a été exécuté contre un modèle ni sur le réseau : l'outil lit des fichiers locaux et n'en modifie aucun.

Légende. **[fichiers]** : recalculé sur les agrégats versés (`pat-19-cache-ttl-replay-v1.json`). **[flux]** : lu dans les journaux de
session de l'hôte et les registres de campagne, qui restent hors dépôt et ne peuvent pas être revérifiés depuis le dépôt. **[doc]** :
fait de la documentation d'Anthropic **lu par le coordinateur le 2026-10-09** (pages `code.claude.com/docs/en/prompt-caching` et `env-vars`), **non revérifié par cet outil**. **[code]** : lu dans le code.

## Résumé

- **Résultat, tel qu'il sort après la correction du 2026-10-09 (voir plus bas) : gain net sous les deux bornes** [flux][fichiers]. Sur les
  104 sessions cloud rejouées (v4 : 30, v5 : 74 ; aucune exclue), le coût de liste catalogue avec écritures « 1 heure » est de **25,06 USD** ;
  le même travail avec un cache « 5 minutes » simulé coûte **20,92 USD (borne prudente)** et **19,87 USD (borne favorable)**, soit **−4,14 USD
  (−16,5 %)** et **−5,20 USD (−20,7 %)**. **Premiers chiffres, avant correction, gardés pour comparaison** : 19,94 USD (prudente, −5,12 USD,
  −20,4 %) et 19,67 USD (favorable, −5,39 USD, −21,5 %). Par campagne, corrigé : v4, 6,15 USD réel, 5,08 (prudente, −17,3 %) et 4,87
  (favorable, −20,8 %) ; v5, 18,91 USD réel, 15,84 (prudente, −16,3 %) et 15,00 (favorable, −20,7 %). Prix de liste d'API comme poids sous un
  abonnement, jamais une facture ni une économie.
- **Les lectures de cache de première requête pèsent sur le résultat, pas sur son sens** : 102 des 104 sessions (v4 : 30 sur 30 ; v5 : 72 sur 74)
  lisent du cache dès leur première requête (340 392 tokens : 94 032 en v4, 246 360 en v5), c'est-à-dire une entrée écrite par une session
  antérieure. La correction les compte toutes comme expirées sous la borne prudente, et seulement 16 sessions (54 044 tokens) sous la borne
  favorable. Sur les 1,056 USD qui séparent les deux bornes, **0,787 USD viennent de ces lectures de première requête et 0,270 USD de l'unique requête du relecteur à 388,9 s** (borne prudente) ; **quelle session a écrit
  l'entrée n'est pas observable** : la borne favorable est une hypothèse, pas une mesure.
- **Dans une session, le risque redouté ne s'est presque pas produit** : sur 970 requêtes qui ont une requête précédente dans leur session,
  **une seule** dépasse 5 minutes sous la borne prudente (écart 388,9 s, 56 192 tokens de cache relus, sur 22 969 518 tokens relus au
  total, 0,24 %), **aucune** sous la borne favorable (écart maximal 214,2 s). Au plus 60 s d'écart : 948 requêtes sur 970 sous la borne
  favorable, 915 sous la prudente.
- **Par session** : 103 sessions sur 104 donnent un gain net sous les deux bornes (avant et après correction). La seule session
  **indécidable entre les deux bornes** est `pat-19-x5compare-1/reviewer-13` (15 requêtes, 0,91 USD réel : −0,16 USD sous la borne
  favorable, +0,11 USD sous la prudente ; avant correction −0,18 et +0,09). Aucune session en perte nette.
- **Par rôle** (toutes campagnes, corrigé, prudente / favorable) : implémenteur −17,6 % / −20,7 % ; correcteur −17,3 % / −23,1 % ; relecteur
  −15,5 % / −19,4 %. Par modèle : Sonnet 5.5 −17,4 % / −21,9 % ; Opus 5.5 −15,5 % / −19,4 % (le relecteur est le seul Opus).
- **Un ticket de changement est justifié** (pour décider et mesurer, pas pour appliquer d'office) : sur ces sessions, passer à
  « 5 minutes » aurait été gagnant en coût de liste sous les deux bornes, d'environ un sixième à un cinquième. **Cela ne dit rien du quota de
  l'abonnement : l'effet est inconnu et non documenté.** La question de transmission du réglage est posée plus bas, non résolue.
- **Ce qui ne se conclut pas** : le résultat vaut pour ces 104 sessions (deux campagnes de 6 et 12 tâches non indépendantes, un passage par
  couple tâche / bras). L'écart prudent maximal observé (388,9 s) montre que le seuil de 300 s est proche pour certaines requêtes du
  relecteur ; une tâche dont les tests durent plus de 10 minutes n'est pas dans ces données, et son comportement est **inconnu**.

## Règle de simulation (écrite avant le calcul ; un seul amendement, daté, voir la section suivante)

Elle est aussi dans la docstring de `cache_ttl_replay.py`, où la clause de la correction du 2026-10-09 (section suivante) est ajoutée à la suite. TTL = 300 s. Une requête est un `message.id` d'assistant (les doublons de
diffusion portent les mêmes compteurs et comptent une fois). Pour la requête `i` d'une session, `f_i` est l'instant de son premier
enregistrement et `e_i` celui de son dernier. L'écart `gap_i` à la requête précédente de la **même session** décide : si `gap_i > TTL`,
le cache est **expiré** pour la requête `i`.

**Approximation à nommer** : l'horodatage d'un enregistrement est celui de l'enregistrement, **pas le début de la requête**, antérieur
d'une latence inconnue. La documentation dit que la durée de vie d'une entrée de cache se mesure depuis le début de la requête qui l'écrit
ou la lit, non depuis la fin de sa réponse, et qu'elle est rafraîchie sans frais à chaque lecture [doc]. D'où deux lectures, jamais un
chiffre seul :

- **borne prudente** (le plus d'expirations) : `gap_i = f_i − e_(i−2)`. Le début réel de la requête `i−1` ne peut précéder la fin de la
  réponse `i−2`, et celui de la requête `i` ne peut suivre `f_i` : c'est un majorant de l'écart réel. Pour la deuxième requête d'une
  session, qui n'a pas d'`e_(i−2)`, la règle initiale prenait `f_1 − f_0`, qui n'est pas un majorant ; voir la deuxième correction ;
- **borne favorable** (le moins d'expirations) : `gap_i = f_i − e_(i−1)`, le temps mort entre la fin de la réponse précédente et le
  premier enregistrement de celle-ci. Elle ignore la durée de la requête précédente, que l'horloge réelle compte : ce n'est **pas** un
  minorant rigoureux.

Coût par requête (`I` entrée, `R` lecture de cache, `W` écriture de cache, `O` sortie ; grille `pricing-breakdown-v1.json` : écriture
5 min = 1,25 × l'entrée, écriture 1 h = 2 ×, lecture du tarif de la grille [doc]) : **réel** = `I`·entrée + `R`·lecture + `W`_1h·écriture_1h +
`W`_5m·écriture_5m + `O`·sortie (la scission des journaux eux-mêmes) ; **simulé, non expiré** = `I`·entrée + `R`·lecture + `W`·écriture_5m +
`O`·sortie ; **simulé, expiré** = `I`·entrée + (`R`+`W`)·écriture_5m + `O`·sortie (ce qu'elle a lu est réécrit au prix « 5 minutes »). La
première requête d'une session n'a pas de précédente et écrit au prix « 5 minutes ». Un écart exactement égal à 300 s n'expire pas
(test).

**Non modélisé** : plusieurs points d'ancrage de cache à durées de vie propres, un cache partagé entre sessions, tout ce que l'hôte ferait
autrement avec un réglage « 5 minutes ». **Sous-agents** : le lecteur ne lit pas l'indicateur `isSidechain` (hors de la liste permise par
FOUNDRY-ADR-0015) ; un sous-agent en ligne serait fondu dans la lignée de sa session (limite). Vérification faite une fois à la main
sur les deux campagnes (563 enregistrements d'assistant en v4, 1 360 en v5) : l'indicateur est faux partout, aucun enregistrement de
sous-agent en ligne [flux].

## Correction du 2026-10-09 : les lectures de cache de première requête

**Cette correction a été faite APRÈS que les premiers chiffres ont été vus**, trouvée par le coordinateur avant la revue ; elle n'a pas
été choisie en fonction du résultat (elle le dégrade, et ne change ni son sens ni le verdict de 103 sessions sur 104). Vérification
refaite ici sur les journaux : 30 sessions sur 30 en v4 (94 032 tokens) et 72 sur 74 en v5 (246 360 tokens) ont `cache_read_input_tokens > 0`
dès leur première requête [flux].

*Défaut de la règle initiale* : elle ne comparait une requête qu'à la précédente de la **même** session. La première requête d'une session
lit pourtant une entrée écrite par une session **antérieure** (préfixe partagé), que « 1 heure » conserve entre deux sessions et
« 5 minutes » pas forcément ; cette lecture restait comptée comme lue, ce qui sous-estimait le coût simulé.

*Clause ajoutée* (docstring de `cache_ttl_replay.py`, mêmes données) : quand la première requête lit du cache et que l'entrée est
expirée, elle est tarifée comme toute requête expirée, `(R + W)` au prix d'écriture « 5 minutes ».

- **Borne prudente** : toute lecture de cache d'une première requête est expirée.
- **Borne favorable** : la lecture est conservée si la session antérieure la plus proche **de la même campagne** qui a utilisé le **même
  alias de modèle** a terminé sa dernière requête de ce modèle au plus 300 s avant le premier enregistrement (« antérieure » : son premier
  enregistrement est plus ancien ; un chevauchement, écart négatif, conserve la lecture) ; sinon, y compris pour la première session de la
  campagne, elle est expirée. C'est décidé par les seuls horodatages, alias de modèle et compteurs (FOUNDRY-ADR-0015).

**Ce qui n'est pas observable** : quelle session antérieure a réellement écrit l'entrée lue. La borne favorable est une **hypothèse**, pas
une mesure. Elle ne voit pas non plus les sessions d'hôte sans rapport avec la campagne qui auraient tourné entre deux sessions de campagne
(limite) ; dans les deux campagnes, aucune session ne démarre pendant qu'une autre est encore en cours (`sessions_started_while_an_earlier_one_was_still_running` = 0
dans le JSON) [fichiers].

Une **deuxième correction** (ci-dessous) a suivi la revue indépendante. Les premiers chiffres restent dans le JSON sous les clés `*_before_correction` (`simulated_5m_usd_before_correction`,
`delta_usd_before_correction`, `delta_share_of_real_before_correction`, `result_before_correction`) et dans le tableau ci-dessous.

### Deuxième correction, après la revue indépendante (2026-10-09)

**Faite après que les chiffres précédents ont été vus**, sur des constats de la revue ; les figures de la règle initiale
(`*_before_correction`) et celles d'après la première correction (`*_after_first_correction`) restent dans le JSON et ci-dessous.

- **B. Borne prudente de la deuxième requête.** `f_1 − f_0` n'était pas un majorant (104 écarts sur 970, un par session). La référence est
  maintenant l'horodatage du dernier enregistrement qui précède le premier enregistrement d'assistant de la session (seul son
  horodatage est lu, FOUNDRY-ADR-0015), car le début réel de la requête 0 ne peut le précéder ; à défaut, `f_0`. Les 104 sessions ont un tel
  enregistrement ; les 104 écarts prudents ont changé de 1,4 à 15,2 s (médiane 2,1 s). Aucun n'a franchi 300 s : les chiffres ne bougent pas
  à l'arrondi près, la distribution par tranche non plus.
- **C. Sessions à plusieurs modèles.** Les caches sont par modèle. Une session à plusieurs alias est listée et exclue (`several_models`)
  plutôt que rejouée avec des lignées fondues. Aucune session de ces données n'en a plus d'un : aucune exclusion, aucun effet.
- **D. Requête sans lecture ni écriture de cache.** Elle ne rafraîchit pas l'entrée. La référence de la requête `i` est la dernière requête
  **antérieure** qui a lu ou écrit du cache, `j` : favorable `f_i − e_j`, prudente `f_i − e_(j−1)` (ou la référence de B si `j = 0`). Une requête
  sans aucune requête antérieure qui lit ou écrit prend le statut d'entrée de la première correction. Dans ces données, **0 requête** n'a
  ni lecture ni écriture de cache : aucun effet.
- **E. Écart entre les bornes**, recalculé : 1,056 USD = 0,787 USD de lectures de première requête + 0,270 USD de la requête du relecteur à
  388,9 s (prudente). Par campagne : v4 0,211 + 0 ; v5 0,576 + 0,270.

| Total, 104 sessions (réel 25,06 USD) | Prudente | Favorable |
|---|---:|---:|
| Règle initiale | 19,94 (−20,4 %) | 19,67 (−21,5 %) |
| Après la première correction | 20,92 (−16,5 %) | 19,87 (−20,7 %) |
| **Après la deuxième correction** | **20,92 (−16,5 %)** | **19,87 (−20,7 %)** |

## Méthode

**Outil** [code] : `python3 -m foundry.cache_ttl_replay` (module `plugins/foundry/tooling/foundry/cache_ttl_replay.py`, tests dans
`tests/test_cache_ttl_replay.py`). Options : `--ledger LEDGER` (répétable : `ledger-<campagne>.jsonl` d'une campagne jouée), `--session-logs-dir`
(journaux de l'hôte, `<dossier>/*/<session>.jsonl`, hors dépôt), `--grid` (défaut : `pricing-breakdown-v1.json`), `--out` (n'écrase jamais).
Code de sortie 0, ou 2 si une entrée est refusée. Il réutilise le lecteur de grille de `cost_breakdown.py`.

- **Jointure** : les sessions viennent des événements `cloud_started` réels (hors essais à blanc) du registre de la campagne ; le journal de
  l'hôte est le fichier `<identifiant>.jsonl` sous le dossier donné, exactement un.
- **Lecteur (FOUNDRY-ADR-0015)** : il extrait seulement le type d'enregistrement, l'horodatage, l'identifiant de message (pour compter une
  requête une fois), l'alias du modèle et les compteurs de tokens (entrée, lecture de cache, écriture de cache et sa scission 5 min / 1 h,
  sortie). Jamais d'invite, de chemin, de commande, de nom d'outil ni d'extrait. Un refus est un code fixe, jamais un message qui porte un
  chemin ou un contenu.
- **Sessions inutilisables** : journal absent ou ambigu, illisible, compteur absent, scission d'écriture qui ne s'additionne pas,
  doublons différents, horodatages non ordonnés, modèle sans prix utilisable le jour de la requête (y compris Haiku 5.5, dont le prix dépend
  de la longueur de l'invite, que les journaux ne portent pas), ou total de tokens différent du `premium_tokens` du registre : elles sont
  **listées et exclues des deux côtés**, jamais estimées. Ici : aucune (104 sur 104 rejouées ; le total de tokens de chaque journal égale
  celui du registre) [flux].
- **Prix** : `pricing-breakdown-v1.json` seul, aucun prix ajouté. Le prix de lecture de cache de Sonnet 5.5 (0,10 selon la page, 0,20 selon
  l'hôte, voir PAT-129) ne touche pas la différence : une lecture non expirée coûte pareil dans les deux mondes ; il ne change que la
  part relative.
- **Noms des sessions** : campagne, rôle et rang dans le registre (`pat-19-x5compare-1/reviewer-13`), jamais l'identifiant brut.
- **Citation de la documentation** (lue le 2026-10-09 [doc]) : écriture « 5 minutes » = 1,25 × le prix d'entrée de base, « 1 heure » = 2 ×
  ; la durée de vie d'une entrée est rafraîchie sans frais à chaque lecture et se mesure depuis le début de la requête qui l'écrit ou la
  lit ; le réglage `promptCacheTtl` ou la variable `CLAUDE_CODE_PROMPT_CACHE_TTL` acceptent `5m` ou `1h` (Claude Code 2.1.242 ou plus) ;
  la conversation principale, `-p` compris, vaut 1 heure par défaut sur un abonnement. Ces faits ont été **lus par le coordinateur le
  2026-10-09 sur `code.claude.com/docs/en/prompt-caching` et `env-vars`, et ne sont pas revérifiés par cet outil**.

## Résultats

Prix de liste d'API en USD, valeurs « prudente / favorable ». Écart = simulé moins réel (négatif = moins cher en « 5 minutes »).
« Avant correction » : la règle sans la clause de première requête. Fichier :
[`pat-19-cache-ttl-replay-v1.json`](pat-19-cache-ttl-replay-v1.json) [fichiers].

| Ensemble | Requêtes | Réel 1 h | Simulé 5 min, corrigé | Écart, corrigé | Écart relatif, corrigé | Simulé avant correction | Résultat |
|---|---:|---:|---:|---:|---:|---:|---|
| v4, total | 333 | 6,15 | 5,08 / 4,87 | −1,07 / −1,28 | −17,3 % / −20,8 % | 4,83 / 4,83 | gain net |
| v5, total | 741 | 18,91 | 15,84 / 15,00 | −3,07 / −3,92 | −16,3 % / −20,7 % | 15,12 / 14,85 | gain net |
| **Les deux, total** | 1 074 | **25,06** | **20,92 / 19,87** | **−4,14 / −5,20** | **−16,5 % / −20,7 %** | **19,94 / 19,67** | **gain net** |
| implémenteur | 394 | 6,18 | 5,09 / 4,90 | −1,09 / −1,28 | −17,6 % / −20,7 % | 4,84 / 4,84 | gain net |
| correcteur | 464 | 6,90 | 5,70 / 5,31 | −1,19 / −1,59 | −17,3 % / −23,1 % | 5,31 / 5,31 | gain net |
| relecteur | 216 | 11,99 | 10,13 / 9,66 | −1,86 / −2,33 | −15,5 % / −19,4 % | 9,80 / 9,53 | gain net |
| `claude-sonnet-5-5` | 858 | 13,08 | 10,80 / 10,21 | −2,28 / −2,87 | −17,4 % / −21,9 % | 10,14 / 10,14 | gain net |
| `claude-opus-5-5` | 216 | 11,99 | 10,13 / 9,66 | −1,86 / −2,33 | −15,5 % / −19,4 % | 9,80 / 9,53 | gain net |

Requêtes au-delà de 300 s *dans une session* (inchangées par la correction) : 1 / 0 (prudente / favorable), écart maximal 389 / 214 s.

Les lignes v4 et v5 par rôle et par modèle sont dans le fichier JSON (clés `by_role`, `by_model`). Toutes les écritures de cache des
journaux sont à « 1 heure » (2 774 429 tokens ; 0 à « 5 minutes ») ; 22 969 518 tokens sont lus.

### Distribution des écarts entre requêtes, par rôle et par modèle

Nombre de requêtes avec précédente, par tranche d'écart, « prudente / favorable » (toutes campagnes) ; les tranches au-delà de 600 s sont
vides, sauf mention.

| Ensemble | ≤ 60 s | 60 à 300 s | 300 à 600 s | Tokens de cache relus sur requêtes > 300 s (prudente / favorable) |
|---|---:|---:|---:|---|
| implémenteur | 342 / 350 | 19 / 11 | 0 / 0 | 0 / 0 sur 8 760 441 |
| correcteur | 410 / 414 | 4 / 0 | 0 / 0 | 0 / 0 sur 8 178 011 |
| relecteur | 163 / 184 | 31 / 11 | 1 / 0 | 56 192 / 0 sur 6 031 066 |
| `claude-sonnet-5-5` | 752 / 764 | 23 / 11 | 0 / 0 | 0 / 0 sur 16 938 452 |
| `claude-opus-5-5` | 163 / 184 | 31 / 11 | 1 / 0 | 56 192 / 0 sur 6 031 066 |

### Lectures de cache de première requête (correction), sessions et tokens concernés

« Sessions » = sessions dont la première requête lit du cache ; « expirées » = celles dont la lecture est comptée expirée, prudente / favorable.
Tokens de cache relus par ces premières requêtes, puis ceux comptés expirés.

| Ensemble | Sessions avec lecture | Tokens lus | Sessions expirées | Tokens expirés |
|---|---:|---:|---|---|
| v4 | 30 | 94 032 | 30 / 4 | 94 032 / 12 674 |
| v5 | 72 | 246 360 | 72 / 12 | 246 360 / 41 370 |
| **Les deux** | **102** | **340 392** | **102 / 16** | **340 392 / 54 044** |
| v4, implémenteur / correcteur / relecteur | 11 / 15 / 4 | 34 342 / 46 830 / 12 860 | 11 / 15 / 4 ; 2 / 0 / 2 | 34 342 / 46 830 / 12 860 ; 6 244 / 0 / 6 430 |
| v5, implémenteur / correcteur / relecteur | 21 / 35 / 16 | 71 421 / 119 035 / 55 904 | 21 / 35 / 16 ; 6 / 0 / 6 | 71 421 / 119 035 / 55 904 ; 20 406 / 0 / 20 964 |
| Sonnet 5.5 (v4 / v5) | 26 / 56 | 81 172 / 190 456 | 26 / 56 ; 2 / 6 | 81 172 / 190 456 ; 6 244 / 20 406 |
| Opus 5.5 (v4 / v5) | 4 / 16 | 12 860 / 55 904 | 4 / 16 ; 2 / 6 | 12 860 / 55 904 ; 6 430 / 20 964 |

Pour les lignes à deux valeurs séparées par « ; », la première est la borne prudente et la seconde la favorable.

Dans une session, part des requêtes au-delà de 5 minutes : 1 sur 970 (0,10 %) prudente, 0 sur 970 favorable. Les écarts par campagne sont dans le JSON.

## Ce qui reste inconnu

- [inconnu] **L'effet sur le quota de l'abonnement** : non documenté, rien n'en est conclu. Un coût de liste n'est pas une consommation de
  quota.
- [inconnu] Le comportement sur une tâche dont une suite de tests dépasse 5 ou 10 minutes : absent de ces 104 sessions (écart prudent maximal
  388,9 s, sur une session de relecteur).
- [inconnu] **Quelle session antérieure a écrit l'entrée lue par une première requête**, et si une session de l'hôte sans rapport avec la campagne l'a
  rafraîchie : la borne favorable de la correction est une hypothèse.
- [inconnu] La latence réelle entre le début d'une requête et son premier enregistrement : d'où l'encadrement par deux bornes, dont aucune
  n'est un encadrement rigoureux de l'écart réel dans les deux sens (voir la règle).
- [inconnu] Si l'hôte, sous un réglage « 5 minutes », ancrerait le cache autrement (plusieurs points d'ancrage) que dans la simulation.
- [inconnu] Ce que valent ces chiffres ailleurs que sur les tâches de ces deux campagnes (6 et 12), un passage par couple tâche / bras.
- [inconnu] La date de sortie des modèles 5.5 et donc la validité de la grille avant le 2026-10-05 (voir PAT-129) ; Haiku 5.5 n'est pas
  pris en compte (prix dépendant de la longueur de l'invite, non dérivable des journaux) ; aucune session de ces campagnes ne l'utilise.

## Faut-il un ticket de changement, et la question de transmission

**Oui, un ticket de changement est justifié** par ces données (gain net sous les deux bornes, 103 sessions sur 104, la dernière indécidable),
à condition qu'il décide aussi de la mesure de l'effet réel et du quota, que ce rejeu ne peut pas voir. **Rien n'est changé ici** : ni
lanceur, ni routage, ni réglage.

**Question de transmission, posée sans être résolue** : l'hôte accepte `promptCacheTtl` (fichier de réglages) ou
`CLAUDE_CODE_PROMPT_CACHE_TTL` (variable d'environnement). Le processus enfant que Foundry lance pour une session Claude ne reçoit qu'une
liste fermée de variables (`HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`, `TMPDIR`, `USER`, AGENTS.md R6) : la variable n'y passerait pas sans
modifier cette liste, alors que R6 l'a fermée pour protéger l'identité OAuth liée à la machine. Un réglage dans un fichier de réglages est
donc la piste à instruire en premier ; reste à décider **quel fichier de réglages** (portée, qui l'écrit, comment il est lu par un
enfant lancé hors d'une session interactive) et si l'identité OAuth en est affectée. Ce ticket doit trancher, avec un essai natif
autorisé par le mainteneur ; aucun essai de ce genre n'a été fait ici.

## Limites

- **104 sessions, tâches non indépendantes (6 et 12)**, deux campagnes, un passage par couple tâche / bras : aucune généralité.
- **Prix de liste d'API utilisés comme poids sous un abonnement** : jamais une facture, jamais une économie ; rien sur le quota.
- **Écart entre les deux bornes** (1,056 USD au total) : 0,787 USD viennent des lectures de cache de première requête (correction du
  2026-10-09 ; 16 sessions contre 102 comptées expirées, hypothèse non observable pour la favorable) et 0,270 USD de l'unique requête du
  relecteur à 388,9 s (borne prudente) ; il ne vient donc pas des seules premières requêtes. Par campagne : v4, 0,211 + 0 ; v5, 0,576 + 0,270.
- **Simulation** : un modèle de cache à une seule lignée par session, deux lectures de l'écart dont aucune n'est exacte (voir la règle) ;
  ni points d'ancrage multiples, ni cache partagé.
- **Même ensemble des deux côtés** : toutes les sessions rejouées le sont des deux côtés ; aucune n'a été exclue, donc aucune différence
  d'ensemble n'est possible ici.
- **Hors périmètre** : la qualité et la durée des sessions sous « 5 minutes » ne sont pas mesurées.

## Pièces versionnées

- `plugins/foundry/tooling/foundry/cache_ttl_replay.py`, `plugins/foundry/tests/test_cache_ttl_replay.py`, grille inchangée
  `pricing-breakdown-v1.json`.
- `pat-19-cache-ttl-replay-v1.json` : agrégats seulement (par session nommée par campagne, rôle et rang, par rôle, par modèle, total, avec
  distribution des écarts), sans chemin, invite, commande, extrait ni identifiant de session brut.
  Reproduction : `python3 -m foundry.cache_ttl_replay --ledger <ledger-v4> --ledger <ledger-v5> --session-logs-dir <journaux> --out <fichier>`
  (les entrées sont hors dépôt).
- Contrôle avant versement : aucune occurrence du préfixe d'un dossier personnel, d'un nom d'utilisateur, d'un identifiant de session ni
  d'un secret dans le JSON ni dans ce document (le test `test_committed_aggregates_are_clean_and_consistent` le vérifie sur le JSON).

## Statut documentaire (R5)

- **Nouveau module avec point d'entrée `python3 -m foundry.cache_ttl_replay`** (options `--ledger` répétable, `--session-logs-dir`, `--grid`,
  `--out`, code de sortie 0 ou 2) : documenté ici (« Méthode ») et dans une section ajoutée à [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md).
  Aucun verbe ni option de `foundry_cli.py` ni du lanceur `local_first_runner` n'a changé.
- **Constantes et vocabulaire publics** : `TTL_SECONDS` (300), la fonction `entry_expiry` et la clause de première requête, les clés `first_request_cache_reads`, `first_requests_with_expired_cache_read`, `sessions_started_while_an_earlier_one_was_still_running` et `*_before_correction`, les deux bornes `prudent` et `favourable`, les tranches d'écart, les
  résultats `net_gain` / `net_loss` / `undecidable_between_the_bounds`, les codes d'exclusion (`log_missing`, `log_ambiguous`,
  `log_unreadable`, `counter_absent`, `cache_write_split_differs`, `request_identity_absent`, `duplicate_differs`, `timestamps_unordered`,
  `timestamp_absent`, `timestamp_unreadable`, `no_request`, `no_usable_price`, `tokens_differ_from_ledger`, `several_models`), les clés `*_after_first_correction`, `gaps_changed_by_second_correction`, `gap_between_the_bounds_usd`, `requests_without_cache_read_or_write` : définis ici. Aucune table de
  routage, clé de configuration, réglage du lanceur ni protocole gelé n'a changé.
- **Nouveaux documents** : ce document et le fichier d'agrégats. **CHANGELOG** : une entrée.
- Protocoles v1 à v5, configurations, résultats, rapports et pièces de preuve : inchangés ; aucun verdict recalculé ni requalifié.
- La transmission du réglage (fichier de réglages ou variable d'environnement) est une question ouverte, non tranchée ni implémentée.
- Le détecteur de FOUNDRY-123 n'est pas livré : ce statut est affirmé ici et vérifié en revue, non appliqué mécaniquement.
