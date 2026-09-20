# FOUNDRY-135 — audit des coûts Codex et Claude

Le lecteur sélectionne un seul flux cumulatif Codex par session, calcule ses incréments, puis sépare l’entrée non cachée du cache avant tarification. Le corpus et la normalisation du cache ont été validés dans l’amendement de FOUNDRY-135. Les montants sont des équivalents API dérivés de la grille gelée, pas des factures.

## Corpus et méthode

- Baseline avant FOUNDRY-135 : `31d14793a0df23859fd35d5c2ce4eecf97a0bc78`.
- Grille inchangée, SHA-256 : `baab0c9b54aac87c13fde79e7e64670513fdce9ace22b22883383a4ad26cefef`.
- Huit plus gros journaux Codex, conformément à la commande de reproduction du ticket. Ils contiennent les six identifiants cités.
- Douze plus gros journaux Claude de premier niveau. Cette sélection reconstruite a été explicitement approuvée et figée avec le manifeste ci-dessous ; elle ne revendique pas un manifeste historique absent.
- Sources stables pendant la lecture, vérifiées par taille et date de modification. Les projections locales conservent les formes natives `event_msg` et `token_usage_record`, uniquement avec compteurs, timestamps, modèles et identités de session. Aucun contenu de conversation, code, commande ou chemin de journal dans ce rapport.
- Oracle indépendant du lecteur produit : calcul Decimal depuis les projections ; sélection de `total_token_usage` par session, sinon `thread_token_usage` ; différences en ordre de fichier et nouveaux segments sur recul du flux choisi. Le mélange des origines de deux flux ne peut pas créer de faux resets.
- Les compteurs bruts servent à détecter les resets. Pour chaque incrément retenu, entrée non cachée = input − cached, cache = cached, sortie = output. Les compteurs de raisonnement restent un sous-ensemble non refacturé de la sortie. Les tokens normalisés et toutes les tranches temporelles conservent les totaux attendus.
- Les modèles, dates et tarifs de contexte de la grille sont respectés. Les catégories de Claude restent additives par message, avec comptabilité explicite des entrées synthétiques exclues et du raisonnement indisponible.
- Validation du vrai point d’entrée CLI pour chacun des vingt journaux, puis par host avec les huit ou douze options `--log`. Les résultats sont comparés à l’oracle Decimal indépendant, complété par le contrôle des compteurs de dernière requête avant de choisir un tarif à seuil ; les projections et la grille sont vérifiées par SHA-256.

## Comparatif avant / après (USD équivalents API)

`Partiel` désigne le sous-total des groupes tarifés : le coût complet reste indisponible pour un journal contenant un modèle non tarifé ou des incréments sans taille de requête attestée pour une grille à seuil de contexte. La colonne intermédiaire isole la correction des cumuls déjà présente dans le commit `0329ae2`.

| Journal | Avant | Cumuls seuls | Après correction | Couverture |
|---|---:|---:|---:|---|
| `75875e23` | 4784.744309 | 4780.654581 | 637.157621 | Tarifé |
| `d49cee61` | 243457.248535 | 12158.630519 | 1329.879627 | Partiel : palier Astra non attesté |
| `24d62d69` | 3339.350171 | 3318.301998 | 445.545518 | Tarifé |
| `22e23d37` | 6503.610033 | 6468.047683 | 743.556803 | Tarifé |
| `36e94fc4` | 2044.235904 | 2024.434242 | 275.257922 | Tarifé |
| `82f022ba` | 1222.922603 | 1219.818388 | 150.988308 | Partiel : `gpt-5.4-mini` non tarifé |
| `409b9a39` | 1844.608879 | 1830.164557 | 222.693837 | Tarifé |
| `bbe3c1a4` | 249995.789283 | 992.724101 | 110.450187 | Partiel : `gpt-reserve` non tarifé |
| `claude-01` | 4317.775879 | 4317.775879 | 4317.775879 | Partiel : `claude-opus-4-8` non tarifé |
| `claude-02` | 1603.242894 | 1603.242894 | 1603.242894 | Tarifé |
| `claude-03` | 2316.713631 | 2316.713631 | 2316.713631 | Tarifé |
| `claude-04` | 346.868163 | 346.868163 | 346.868163 | Tarifé |
| `claude-05` | 1849.047356 | 1849.047356 | 1849.047356 | Tarifé |
| `claude-06` | 223.785919 | 223.785919 | 223.785919 | Tarifé |
| `claude-07` | 329.010018 | 329.010018 | 329.010018 | Tarifé |
| `claude-08` | 783.595593 | 783.595593 | 783.595593 | Tarifé |
| `claude-09` | 967.823236 | 967.823236 | 967.823236 | Tarifé |
| `claude-10` | 421.612151 | 421.612151 | 421.612151 | Tarifé |
| `claude-11` | 146.885866 | 146.885866 | 146.885866 | Tarifé |
| `claude-12` | 134.873525 | 134.873525 | 134.873525 | Tarifé |

Somme des sous-totaux connus calculés journal par journal : Codex **513192.509717 → 3915.529823 USD** ; Claude **13441.234231 → 13441.234231 USD**. Ensemble : **526633.743948 → 17356.764054 USD**. Le total complet demeure indisponible à cause des trois modèles non tarifés et du palier Astra non attesté. Les vingt résultats du lecteur concordent avec l’oracle indépendant au microdollar après arrondi par groupe.

La commande unique avec les huit journaux Codex rend un sous-total connu de **3898.498571 USD**. L’agrégat public par modèle propage l’indisponibilité : le groupe Astra entier devient indisponible, y compris **17.031252 USD** attestés dans d’autres journaux. Cette règle préexistante explique la différence avec la somme des sorties séparées ; ce montant n’est pas perdu dans les compteurs. Avec Claude (**13441.234231 USD** inchangés), les sous-totaux des deux commandes groupées valent **17339.732802 USD**. L’oracle contrôle aussi cette propagation par modèle.

## Vérifications manuelles

`75875e23` porte 6465 relevés globaux sans reset : input 862585843, cached input 828699392, output 1779189. L’entrée non cachée vaut donc 33886451. Le journal indique Sol : `(33886451 × 5 + 828699392 × 0.5 + 1779189 × 30) / 1000000 = 637.157621 USD`. Le dernier cumul et la somme des différences donnent les mêmes compteurs.

Les 254.8630484 USD du tableau historique s’obtiennent avec les mêmes compteurs normalisés au tarif Terra uniforme. Cette hypothèse ne correspond pas au modèle enregistré ; l’amendement validé retient les modèles réellement observés. Les journaux d49cee61 et bbe3c1a4 combinent plusieurs modèles, donc un unique tarif appliqué au dernier cumul ne remplace pas la ventilation.

Les flux globaux de 82f022ba et bbe3c1a4 présentent respectivement 4 et 28 diminutions. Le calcul additionne les fins de segments, conformément au critère de reset, plutôt que de ne retenir que le dernier snapshot du fichier.

Pour Claude, `claude-02` contient 5708 messages facturables et 413 diminutions de l’entrée entre messages successifs ; sa somme indépendante donne 1603.242894 USD. `claude-03` contient 7562 messages facturables, 5 entrées synthétiques exclues et 427 diminutions ; sa somme donne 2316.713631 USD. Ces variations correspondent à des compteurs par message, contrairement au cumul global Codex. Les résultats CLI et diagnostics des douze journaux Claude sont identiques avant/après.

Dans `d49cee61`, les incréments Astra du 2026-09-05 ne concordent pas tous avec les compteurs concurrents de dernière requête. Les **70.940254 USD** précédemment calculés supposent donc une taille de requête non attestée ; ils deviennent indisponibles. Le sous-total connu du journal vaut **1329.879627 USD**, avec conservation intégrale des compteurs. La régression synthétique `total=100,last=100` puis `total=230,last=80` conserve 230 tokens et refuse de donner un rang au fragment de 130 tokens.

La grille, les alias, la structure des courbes et `reader_completeness` sont conservés. Les coûts par position et seuil utilisent les mêmes catégories normalisées que les totaux ; lorsqu’une position de requête manque, elle reste indisponible.

## Provenance du corpus figé

SHA-256 des sources et de leurs projections minimales. Les projections demeurent locales et ne sont pas commitées.

| Journal | SHA-256 source | SHA-256 projection |
|---|---|---|
| `75875e23` | `acc81e7f0e87866a33e970e48f4a43352b27f93ce39267724d101d66a0a6fb21` | `3de19cd02680a0dba5218f22da7ba67c3927a0e45de1c447ac5dc4f63c401ce4` |
| `d49cee61` | `f9f83ee58e255d11278d407f45c578ba87e9b5c6b6732a4f97da9066b120e82c` | `3a5627c1022968e49551d8fb0775c475c2e9e59d7f8653dad4731011114617cb` |
| `24d62d69` | `fc9f9ea1399ce3692eac8f649f9e672e8763e9bb12e0c44a7f019bdc5ed2d6ea` | `d3a70ade074bb4858369e55a33842b58827ebab6d91271364fa255d1d0dbf508` |
| `22e23d37` | `4a52bd2f613d8e4c2d69bbb8a6113377f86dd2a3697d09b2e6ffcebd0b3cdb7b` | `2c571db040b786cf077b385f3f849c2e429500ae71c305f22b7781dc34d65f5e` |
| `36e94fc4` | `49b78923a17c103ef2f3cbcefa8721e8aef5ffd83d0bf9d2aa6ac7f2f439a2ed` | `370095d31aa108b76210e7fd1bfd023cd1d293e26142df06071d6b40ee6680a5` |
| `82f022ba` | `febb4fe8e76f0a53b35468c3cd338befec72e5ec95e99880bd5d13f5130d184f` | `3f76832e17422825a2ae59926a36eba8057e23cfe5f1d2cd685ef19ae827913f` |
| `409b9a39` | `dbbd40dd8b4d2e875fd2c07a40bb313dd95bdaf53457cb112ba0607499fdfc55` | `5f1ee51c844601a9b3c7d88da5f1a2876ba0638f79abeddce177eacb9ef8cbfd` |
| `bbe3c1a4` | `855f4eb720a3bcde8710a3906e6820b04f0e6ba65ba748a82c4dbdc9dbe98da9` | `a14083a12f94e1f6de516f0979fe62400fc3da4c32c3c26a8ecfc399a77ccb3b` |
| `claude-01` | `7c8497e282a367394d74f99d709bbfca5741f5b1b5ef7e83e5811482ca78b641` | `5a0bf0a74f9d13b8e316667c8e90d59076660e5f62e00dc90e4048c062a35d5d` |
| `claude-02` | `7334c95af28d6196c77e9caad2c9cc7adc6df06891f12d7de40860810862dc5f` | `da7833907ad766ab9402406151478c2d02b2a336393a0e50b90500bf4a6850d2` |
| `claude-03` | `4968df0dd6ed44348599c3d67427a72c7ac209ee09ff6eef0646841861710b85` | `e52134b0f5e060bcc14981253ccdfc58bafd7da91204f26df78e25d9c53b27f5` |
| `claude-04` | `35cd36e68234de3c0630f0b2b3345b1b48dcdab66340757e84c8ff66c7f496a6` | `54092d4839eb21676e33e4c802ca05cd7144412b2290b4fbfd6f0915e9a91326` |
| `claude-05` | `ec32c6d3f0d1bf5b228838493da1868ef1d28d992f515c743d45e7d048ebf004` | `9fd966b946a36160521750fe517255ba4e2152c91ffa3f536947fd8146053e53` |
| `claude-06` | `4933268ad478342fd658b956cd707c8d022a7ce2a6cc85d57d42902a3a2e9717` | `26ab3311de27ec784152400224034f5850c309f2fc8d1b7121f36a4e7ef14aec` |
| `claude-07` | `92f83d8f0eb7d083059ea3335addbcfc4b4299e0186820ec7389b04ebe313819` | `1fb344276a57518c187a908ea5dc160b6d145dbf6cdc8349fac04d10c8f3ad1c` |
| `claude-08` | `b3f06a6b206a263332c1c21c21e5318f19fa8b8bd6766c513269c6b4b780fb9d` | `47ed97429353718a80ff31cc433029fe2be7cb6b9057f6f22741b4e15ca2c757` |
| `claude-09` | `aeaffe62935912e1f01f1a5ead8e4bf1c8d74c324276be0fd5508b32fdff45d5` | `738dc32cfa150caa0c8c0e75296a170425fc03768650e1cdc61f07fa792b306f` |
| `claude-10` | `151a930ecdae18c299b7ffef3343a27493fa44ce007ca092352c03a94db265b8` | `60af9b42686a65933ea91fbe285db176ca293ffae280b5495a6915002ac238c6` |
| `claude-11` | `5dbb00a2e562b4149cddb4a31c7e3841019937da4d4d0120f37662a96f8b300e` | `be4b88fe45a4d139ae8ebd79f8c04b6a167c89ed9e04988cab3bdc15ae97f1f8` |
| `claude-12` | `4dfdf96f0280072e2424140e65279c5f3a615757ce2e35bd084fee7bbf2c0e91` | `2cd4ff40cacca0d6e0322e319bf8aeec2ebf7cbfcf5d9bf49504f83228fb3ffa` |
