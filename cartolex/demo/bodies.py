# SPDX-License-Identifier: MIT
"""Body texts for demo works: long, repetitive, with generic filler.

A work's body elaborates its abstract: an introduction, methods, results and
a discussion, each a heading and a few paragraphs drawn *with replacement*
from a small pool of sentence templates, plus figure and table captions. The
focal terms of the abstract come back again and again, among generic phrasing
("the present study", "the data set", "a significant difference"), as in a
full text. Bodies exist so that choices that matter mostly for full texts
(presence or frequency votes, weights of text parts) can be measured.

Bodies are written from a random stream of their own, after every work is
made: a world with bodies has the same people, bibliography, titles and
abstracts as the world without them.
"""

from __future__ import annotations

import random

from .texts import TextPlan, _Filler, elide, render, slot_names

#: Section order of a body.
SECTIONS = ("introduction", "methods", "results", "discussion")

HEADINGS: dict[str, dict[str, str]] = {
    "en": {
        "introduction": "Introduction",
        "methods": "Material and methods",
        "results": "Results",
        "discussion": "Discussion",
    },
    "fr": {
        "introduction": "Introduction",
        "methods": "Matériel et méthodes",
        "results": "Résultats",
        "discussion": "Discussion",
    },
    "pt": {
        "introduction": "Introdução",
        "methods": "Material e métodos",
        "results": "Resultados",
        "discussion": "Discussão",
    },
}

BODY_TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "introduction": (
            "{^T} has received increasing attention over the last decades, in particular in the context of {D}.",
            "Previous studies of {T} have mostly focused on single sites, and the role of {T2} remains poorly documented.",
            "The present study addresses this gap by combining {M} with {M2}.",
            "An understanding of {T} requires long observation periods and a careful treatment of {T2}.",
            "In this paper, the term {T} refers to the processes described in the studies cited above.",
            "The objectives of the present study are threefold.",
            "First, we describe {T} {S}; second, we quantify its link to {T2}; third, we discuss the implications of {D}.",
            "The remainder of the paper is organised as follows.",
            "Several authors have stressed the need for a better description of {T}.",
        ),
        "methods": (
            "The study area is located {S}.",
            "Data were collected between {Y1} and {Y2} at {N} sampling sites.",
            "All measurements of {T} were made with {M}.",
            "The data set was checked for quality before the analysis.",
            "Missing values were removed from the data set.",
            "Statistical analyses were performed with {M}.",
            "The significance level was set at 5 %.",
            "Uncertainties were estimated with {M2}.",
            "The sampling design followed the protocol described in previous studies.",
            "{^T} was estimated at each site and for each sampling period.",
            "A sensitivity analysis was carried out for the main parameters of the model.",
        ),
        "results": (
            "Figure {N} shows the spatial distribution of {T} {S}.",
            "Table {N} summarises the main characteristics of {T} for the study period.",
            "The results show a significant difference in {T} between sites.",
            "The mean value of {T} was {P} % higher during the second half of the study period.",
            "No significant difference in {T2} was found between the sampling periods.",
            "{^T} was strongly correlated with {T2} at most sites.",
            "The relation between {T} and {T2} was weaker at the remaining sites.",
            "About {P} % of the variance in {T} was explained by {T2}.",
            "The highest values of {T} were observed {S}.",
            "These results are consistent with the patterns of {T} described in the literature.",
        ),
        "discussion": (
            "The results of the present study confirm the importance of {T} for {T2}.",
            "Several limitations of the present study should be noted.",
            "First, the study period was relatively short; second, the number of sites was limited.",
            "The response of {T} to {D} deserves further attention.",
            "Future work should combine {M} with long-term observations of {T}.",
            "In conclusion, {T} plays a major role {S}.",
            "These findings have practical implications for the management of {T}.",
            "Further studies are needed to generalise these results to other settings.",
        ),
        "captions": (
            "Figure {N}. {^T} {S}.",
            "Figure {N}. Relation between {T} and {T2}.",
            "Table {N}. Main characteristics of {T} and {T2}.",
        ),
    },
    "fr": {
        "introduction": (
            "La question {de:T} fait l'objet d'une attention croissante depuis plusieurs décennies, en particulier dans le contexte {de:D}.",
            "Les travaux antérieurs sur {T} ont surtout porté sur des sites isolés, et le rôle {de:T2} reste mal documenté.",
            "La présente étude comble cette lacune en associant {M} et {M2}.",
            "La compréhension {de:T} exige de longues périodes d'observation et une prise en compte attentive {de:T2}.",
            "Dans cet article, l'expression {T} désigne les processus décrits dans les travaux cités plus haut.",
            "Les objectifs de la présente étude sont au nombre de trois.",
            "Nous décrivons d'abord {T} {S}, puis nous quantifions son lien avec {T2}, et nous discutons enfin les effets {de:D}.",
            "La suite de l'article est organisée comme suit.",
            "Plusieurs auteurs ont souligné la nécessité d'une meilleure description {de:T}.",
        ),
        "methods": (
            "La zone d'étude est située {S}.",
            "Les données ont été recueillies entre {Y1} et {Y2} sur {N} sites d'échantillonnage.",
            "Toutes les mesures {de:T} ont été réalisées à l'aide {de:M}.",
            "Le jeu de données a été contrôlé avant l'analyse.",
            "Les valeurs manquantes ont été retirées du jeu de données.",
            "Les analyses statistiques ont été réalisées à l'aide {de:M}.",
            "Le seuil de signification a été fixé à 5 %.",
            "Les incertitudes ont été estimées à l'aide {de:M2}.",
            "Le plan d'échantillonnage suit le protocole décrit dans les études précédentes.",
            "L'estimation {de:T} a été faite pour chaque site et chaque période d'échantillonnage.",
            "Une analyse de sensibilité a été menée sur les principaux paramètres du modèle.",
        ),
        "results": (
            "La figure {N} présente la distribution spatiale {de:T} {S}.",
            "Le tableau {N} résume les principales caractéristiques {de:T} sur la période d'étude.",
            "Les résultats montrent une différence significative {de:T} entre les sites.",
            "La valeur moyenne {de:T} est supérieure de {P} % pendant la seconde moitié de la période d'étude.",
            "Aucune différence significative {de:T2} n'est observée entre les périodes d'échantillonnage.",
            "L'évolution {de:T} est fortement corrélée à celle {de:T2} sur la plupart des sites.",
            "La relation entre {T} et {T2} est plus faible sur les autres sites.",
            "Environ {P} % de la variance {de:T} est expliquée par {T2}.",
            "Les valeurs les plus élevées {de:T} sont observées {S}.",
            "Ces résultats concordent avec les tendances {de:T} décrites dans la littérature.",
        ),
        "discussion": (
            "Les résultats de la présente étude confirment l'importance {de:T} pour {T2}.",
            "Plusieurs limites de la présente étude doivent être soulignées.",
            "D'une part, la période d'étude est relativement courte ; d'autre part, le nombre de sites est limité.",
            "La réponse {de:T} {a:D} mérite une attention particulière.",
            "Les travaux futurs devraient associer {M} à des observations à long terme {de:T}.",
            "En conclusion, le rôle {de:T} est majeur {S}.",
            "Ces résultats ont des implications pratiques pour la gestion {de:T}.",
            "D'autres études sont nécessaires pour généraliser ces résultats à d'autres contextes.",
        ),
        "captions": (
            "Figure {N}. {^T} {S}.",
            "Figure {N}. Relation entre {T} et {T2}.",
            "Tableau {N}. Principales caractéristiques {de:T} et {de:T2}.",
        ),
    },
    "pt": {
        "introduction": (
            "A questão {de:T} tem recebido atenção crescente nas últimas décadas, em particular no contexto {de:D}.",
            "Os estudos anteriores sobre {T} concentraram-se sobretudo em sítios isolados, e o papel {de:T2} continua pouco documentado.",
            "O presente estudo preenche essa lacuna ao combinar {M} e {M2}.",
            "A compreensão {de:T} exige longos períodos de observação e uma consideração cuidadosa {de:T2}.",
            "Neste artigo, a expressão {T} designa os processos descritos nos trabalhos citados acima.",
            "Os objetivos do presente estudo são três.",
            "Primeiro, descrevemos {T} {S}; depois, quantificamos sua relação com {T2}; por fim, discutimos os efeitos {de:D}.",
            "O restante do artigo está organizado da seguinte forma.",
            "Vários autores destacaram a necessidade de uma melhor descrição {de:T}.",
        ),
        "methods": (
            "A área de estudo está localizada {S}.",
            "Os dados foram coletados entre {Y1} e {Y2} em {N} pontos de amostragem.",
            "Todas as medições {de:T} foram feitas por meio {de:M}.",
            "O conjunto de dados foi verificado antes da análise.",
            "Os valores ausentes foram retirados do conjunto de dados.",
            "As análises estatísticas foram realizadas por meio {de:M}.",
            "O nível de significância foi fixado em 5 %.",
            "As incertezas foram estimadas por meio {de:M2}.",
            "O desenho amostral seguiu o protocolo descrito em estudos anteriores.",
            "A estimativa {de:T} foi feita para cada sítio e cada período de amostragem.",
            "Uma análise de sensibilidade foi realizada para os principais parâmetros do modelo.",
        ),
        "results": (
            "A figura {N} mostra a distribuição espacial {de:T} {S}.",
            "A tabela {N} resume as principais características {de:T} no período de estudo.",
            "Os resultados mostram uma diferença significativa {de:T} entre os sítios.",
            "O valor médio {de:T} foi {P} % maior na segunda metade do período de estudo.",
            "Nenhuma diferença significativa {de:T2} foi encontrada entre os períodos de amostragem.",
            "A evolução {de:T} esteve fortemente correlacionada com a {de:T2} na maioria dos sítios.",
            "A relação entre {T} e {T2} foi mais fraca nos demais sítios.",
            "Cerca de {P} % da variância {de:T} foi explicada {por:T2}.",
            "Os valores mais altos {de:T} foram observados {S}.",
            "Esses resultados são coerentes com os padrões {de:T} descritos na literatura.",
        ),
        "discussion": (
            "Os resultados do presente estudo confirmam a importância {de:T} para {T2}.",
            "Algumas limitações do presente estudo devem ser destacadas.",
            "Por um lado, o período de estudo foi relativamente curto; por outro, o número de sítios foi limitado.",
            "A resposta {de:T} {a:D} merece atenção especial.",
            "Trabalhos futuros deveriam combinar {M} com observações de longo prazo {de:T}.",
            "Em conclusão, o papel {de:T} é central {S}.",
            "Esses resultados têm implicações práticas para a gestão {de:T}.",
            "Outros estudos são necessários para generalizar esses resultados a outros contextos.",
        ),
        "captions": (
            "Figura {N}. {^T} {S}.",
            "Figura {N}. Relação entre {T} e {T2}.",
            "Tabela {N}. Principais características {de:T} e {de:T2}.",
        ),
    },
}

#: Paragraphs per section, and sentences per paragraph.
PARAGRAPHS = {"introduction": 2, "methods": 2, "results": 3, "discussion": 2}
SENTENCES = (4, 7)


def compose_body(rng: random.Random, plan: TextPlan, filler: _Filler) -> str:
    """The body of a work: sections of paragraphs, and captions, separated by blank lines.

    *filler* is the one that wrote the work's title and abstract (its focal
    terms come back here); *rng* is the bodies' own stream.
    """
    filler.rng = rng
    lang = plan.language
    templates = BODY_TEMPLATES[lang]

    def sentence(template: str) -> str:
        text = render(template, filler.slots(slot_names(template)), lang)
        return elide(text) if lang == "fr" else text

    paragraphs: list[str] = []
    for section in SECTIONS:
        paragraphs.append(HEADINGS[lang][section])
        for _ in range(PARAGRAPHS[section]):
            n = rng.randint(*SENTENCES)
            paragraphs.append(" ".join(sentence(rng.choice(templates[section])) for _ in range(n)))
        if section == "results":
            paragraphs += [sentence(rng.choice(templates["captions"])) for _ in range(2)]
    return "\n\n".join(paragraphs)
