# IMC Prosperity 4 — Stratégie de Trading Algorithmique
## Document de Préparation Entretien — Edgar Cornillet, Chef d'Équipe

---

## 1. Contexte de la Compétition

IMC Prosperity 4 est une compétition internationale de trading algorithmique organisée par IMC Trading. Les participants conçoivent des algorithmes en Python pour trader des produits sur des marchés simulés en temps réel, face à des bots aux comportements variés. L'objectif est de maximiser le Profit & Loss (PnL) sur plusieurs jours de trading.

Au Round 1, deux produits étaient disponibles : **Intarian Pepper Root** (limite de position : 80) et **Ash Coated Osmium** (limite : 80). Nous disposions de données historiques sur 3 jours pour des produits similaires (Rainforest Resin, Kelp, Squid Ink).

**Résultat final : 100 994 XIRECs, rang 325 sur le leaderboard algorithmique.**

---

## 2. Architecture de Base : le Market Maker V3

### 2.1 Le Principe du Market Making

Un market maker fournit de la liquidité au marché en postant simultanément des ordres d'achat (bids) et de vente (asks). Le profit vient de la différence entre les prix d'achat et de vente — le **spread capturé**. C'est une stratégie non-directionnelle : on ne parie pas sur la direction du marché, on profite du flux d'ordres.

### 2.2 Les Composantes Techniques

Notre algorithme V3 reposait sur cinq piliers :

**a) Fair Value par EMA (Exponential Moving Average)**
On estime la "juste valeur" du produit via une EMA du prix pondéré par les volumes (WMID — Volume-Weighted Mid). L'alpha de 0.15 offre un compromis entre réactivité (suivre les mouvements) et stabilité (filtrer le bruit). Le WMID donne un mid-price plus précis que la simple moyenne bid/ask, car il intègre l'asymétrie des volumes dans le carnet.

**b) Gestion d'Inventaire par Skew Exponentiel**
Quand on accumule une position longue ou courte, on s'expose au risque directionnel. Le skew d'inventaire ajuste notre fair value dans la direction qui réduit la position. La formule `inv_ratio × (1 + 2 × inv_ratio²)` est **exponentielle** : la pénalité est faible pour les petites positions mais explose près des limites. Cela empêche les positions extrêmes qui nous bloqueraient.

**c) Cotation Multi-Niveaux (Penny + Théorique)**
On poste des ordres à deux niveaux de prix. Le niveau 1 (60% du budget) utilise le minimum entre le "penny" (best_bid + 1) et le prix théorique (adj_fair ± spread). Le niveau 2 (40%) poste 1 tick plus profond. Cette approche maximise la probabilité de fill tout en capturant du spread.

**d) Spread Adaptatif à la Volatilité**
Le base_spread s'élargit quand la volatilité augmente (marché agité → plus de risque → on demande plus de compensation) et se resserre quand elle diminue. On mesure la volatilité via une EMA des variations absolues de prix.

**e) Layer de Taking Agressif**
Quand le carnet montre un prix manifestement sous-évalué par rapport à notre fair value (ask < adj_fair), on "prend" la liquidité immédiatement au lieu d'attendre passivement.

### 2.3 Performance V3

Score : **~4 883** sur le sample de test (1K ticks). C'était notre point de départ solide, applicable de manière générique aux deux produits.

---

## 3. L'Analyse des Logs : la Rupture Méthodologique

### 3.1 La Démarche Data-Driven

La vraie progression est venue de l'analyse des **logs de simulation** (128578.log). Au lieu de modifier l'algorithme à l'intuition, on a extrait des insights quantitatifs de chaque trade, chaque tick, chaque mouvement du carnet d'ordres.

### 3.2 Les Découvertes Clés

**Découverte 1 : Les spreads réels sont 5× plus larges que les données historiques.**
Les données historiques montraient des spreads de 2-3 ticks. Les données réelles montraient Pepper Root à 13 et Osmium à 16. Cela changeait fondamentalement l'économie de chaque trade.

**Découverte 2 : Les spike bots sont informés — le taking est négatif en EV.**
On a analysé les 71 "spikes" (moments où le bid/ask sautait de >5 ticks). En mesurant le PnL moyen 5 ticks après chaque spike trade, on a trouvé **-1.1 par trade, seulement 33% profitable**. Les bots qui placent des ordres agressifs *savent* que le prix va bouger dans leur direction. Trader contre eux est perdant.

C'est cette découverte qui expliquait pourquoi nos versions agressives (V4, V5, V6) perdaient de l'argent : elles prenaient plus de spikes, mais chaque spike était négatif en espérance.

**Découverte 3 : Le Pepper Root a un drift constant de +101 ticks sur 100K ticks.**
En traçant le prix du Pepper Root, on a identifié un drift haussier linéaire et constant (+52 première moitié, +49 deuxième moitié). L'Osmium était parfaitement stable autour de 10 000. C'était le seul levier d'optimisation non exploité.

**Découverte 4 : L'utilisation de nos limites de position n'était que de 12%.**
Position max atteinte : ~10 sur un limit de 80. Le bottleneck n'était pas notre capacité mais la fréquence à laquelle les bots croisaient nos prix (142 trades en 100K ticks).

---

## 4. L'Innovation : le Trend Capture avec Confidence Gate (V9d)

### 4.1 Le Concept

L'idée centrale : sur un produit qui drift, on peut capturer le mouvement directionnel **en plus** du profit de market making, en biaisant notre fair value dans la direction du trend. Quand Pepper Root monte, on décale adj_fair vers le haut → on achète plus facilement et on vend moins → on accumule une position longue → on profite du drift.

### 4.2 L'Implémentation Technique

**Détection du trend :** Deux EMA de vitesses différentes — une rapide (alpha=0.15) et une lente (alpha=0.02, ~50 ticks de mémoire). La différence `fast_EMA - slow_EMA` donne le signal de trend. Positif = uptrend, négatif = downtrend.

**Le Confidence Gate (innovation clé) :** On ne veut pas réagir à du bruit. On calcule `trend_confidence = |trend| / volatilité`. Un trend fort dans un marché calme donne une confiance haute ; un trend faible dans un marché agité donne une confiance basse. Le bias et le skew s'adaptent :

| Régime | Confidence | Bias | Skew | Comportement |
|--------|-----------|------|------|-------------|
| Fort trend, calme | 1.0 | ×7.0 | 1.5 | Agressif, accumule la position |
| Pas de trend | 0.0 | ×0.0 | 3.0 | Retombe sur le V3 pur |
| Trend faible, choppy | 0.25 | ×1.2 | 2.6 | Prudent |

**Skew adaptatif :** Quand la confiance est haute, on réduit le skew d'inventaire (3.0 → 1.5) pour permettre de tenir une grosse position directionnelle. Quand la confiance est basse, le skew reste fort pour protéger.

### 4.3 La Progression des Scores

| Version | Score | Changement | Insight |
|---------|-------|-----------|---------|
| V3 | 4 883 | — | MM générique, baseline |
| V9 (×3) | 5 775 | +18% | Premier trend bias, conservateur |
| V9b (×5) | 7 664 | +57% | Bias plus fort, skew réduit |
| V9c (×7) | 9 202 | +88% | Bias maximum, position 78/80 |
| V9d (×7 + gate) | 9 202 | +88% | Même perf + protection anti-choppy |

V9d est identique à V9c sur un marché trending mais se protège automatiquement sur un marché sans tendance. C'est la version qu'on a soumise.

### 4.4 La Gestion du Risque d'Overfitting

Le principal risque était de calibrer les paramètres (×7, skew 1.5) sur un seul sample. Notre protection :

- **Le confidence gate** : si le marché ne trend pas, le bias tombe à 0 et on retombe sur le V3 (~4 800). Le plancher est garanti.
- **Séparation par produit** : l'Osmium n'a aucun trend bias (identique au V3). Seul Pepper Root a le trend capture, car c'est le seul avec un drift documenté.
- **Validation qualitative** : les tips in-game d'Orin confirmaient "slow growth creates structure" pour Pepper Root, validant notre hypothèse de drift.

---

## 5. Le Challenge Manuel : Optimisation d'Auction

### 5.1 Le Problème

Deux produits en enchère (Dryland Flax, Ember Mushroom) avec des carnets d'ordres figés. On est le dernier à soumettre → nos ordres influencent le clearing price. Après l'auction, le Merchant Guild rachète à prix fixe.

### 5.2 La Méthodologie

On a codé un **simulateur d'auction** qui, pour chaque couple (prix, volume) possible :
1. Recalcule le clearing price (max volume traded, tie-break par prix le plus haut)
2. Calcule notre fill (en tenant compte de la priorité — on est dernier)
3. Calcule le profit net (clearing price vs buyback price)

### 5.3 Le Résultat

- **Dryland Flax :** BUY à 29, volume 5 000 → pousse le clearing de 28 à 29, profit 5 000
- **Ember Mushroom :** BUY à 18, volume 35 000 → pousse le clearing de 15 à 18, profit 66 500

L'insight clé d'Ember Mushroom : en plaçant un gros volume d'achat, on **déplace** le clearing price vers le haut (de 15 à 18), mais on reste en-dessous du buyback (20). C'est l'application directe du conseil d'Orin : "volume tips the scale."

---

## 6. Les Leçons Clés (Points d'Entretien)

### Ce que j'ai appris sur le trading quantitatif :

1. **Data over intuition.** Chaque décision a été validée par les données. Les versions "intuitives" (V4 = dual EMA, V5 = size bias, V6 = aggressive penny) ont toutes perdu de l'argent. C'est l'analyse des logs qui a révélé que les spike bots étaient informés — une découverte qui a orienté toute la stratégie.

2. **L'adverse selection est le vrai ennemi du market maker.** Sur un marché avec des participants informés, être plus agressif n'est pas mieux. Le V3 "conservateur" battait toutes les versions agressives parce qu'il évitait de trader contre des bots qui savaient quelque chose qu'on ne savait pas.

3. **Le risk management conditionnel bat le risk management statique.** Le confidence gate adapte dynamiquement le risque au régime de marché. C'est plus sophistiqué qu'un stop-loss ou une limite fixe.

4. **La microstructure de marché détermine la stratégie.** Les mêmes produits sur des marchés différents (spreads de 3 vs spreads de 16) demandent des approches radicalement différentes. On ne peut pas appliquer une stratégie "textbook" sans comprendre la microstructure spécifique.

5. **Leadership d'équipe.** En tant que chef d'équipe de 4 personnes, j'ai coordonné l'analyse des données, les décisions stratégiques et les choix entre versions concurrentes sous contrainte de temps (48h par round).
