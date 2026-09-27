# SPDX-License-Identifier: MIT
"""Titles and abstracts from templates filled with theme terms.

Templates are written so that a term never governs an agreement (it is never
the subject of a present-tense verb, never qualified by an adjective that
agrees with it): terms can then be singular or plural, masculine or feminine,
and every sentence stays grammatical. French and Portuguese templates use the
article forms of :class:`~cartolex.demo.vocabulary.Term` (``{de:T}`` gives
``du transport``, ``de la dérive``, ``de l'érosion`` or ``des vagues``; in
Portuguese ``do transporte``, ``da deriva``, ``dos bancos``, ``das praias``).

Placeholders: ``T`` and ``T2`` theme terms, ``M`` and ``M2`` methods, ``D`` a
driver, ``S`` a setting (a whole locative phrase), ``N`` a count, ``P`` a
percentage, ``Y1`` and ``Y2`` years. ``{^X}`` capitalises the first letter;
``{de:X}`` and ``{a:X}`` are the forms after *de* and *à* (French) or *de* and
*a* (Portuguese); ``{em:X}`` and ``{por:X}`` the Portuguese forms after *em*
and *por* (``no``, ``pela`` …).
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .vocabulary import Method, Setting, Term, Theme

MIN_WORDS = 120
MAX_WORDS = 250

# Roles in the order they appear in an abstract.
ROLES = ("context", "gap", "aim", "data", "result", "implication")
# Roles whose templates are clauses completing a lead-in ("We find that …").
CLAUSE_ROLES = ("result", "implication")

# Writing rules, so that the keyword filters see prose the way they see real
# abstracts: a sentence opens with a function word the filters reject ("In",
# "We", "Our", "La", "Nous", "Ces" …) so no keyword spans two sentences; a
# slot is bordered by such words where the grammar allows; and each role has
# many variants, so no phrase of the templates recurs often in one person's
# texts. Lead-ins are shared by every work of a language.
LEADINS: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "result": (
            "Our results show that",
            "We find that",
            "It appears that",
            "Our analysis indicates that",
            "These data suggest that",
            "We observe that",
            "On the whole, we find that",
            "In sum, our observations show that",
            "As expected, we find that",
            "Of note,",
            "These analyses reveal that",
            "Our estimates indicate that",
            "It is striking that",
            "In most cases, we find that",
        ),
        "implication": (
            "These findings suggest that",
            "Our results imply that",
            "We conclude that",
            "It follows that",
            "We argue that",
            "This suggests that",
            "In practice, this means that",
            "These results indicate that",
        ),
    },
    "fr": {
        "result": (
            "Nos résultats montrent que",
            "Nous constatons que",
            "Il apparaît que",
            "Notre analyse indique que",
            "Ces données suggèrent que",
            "Nous observons que",
            "Au total, il apparaît que",
            "Il ressort de nos analyses que",
            "Comme attendu, nous constatons que",
            "Il est frappant de constater que",
            "Nos estimations indiquent que",
            "Ces analyses révèlent que",
        ),
        "implication": (
            "Ces résultats suggèrent que",
            "Nous en concluons que",
            "Il en découle que",
            "Nous soutenons que",
            "Cela signifie que",
            "En pratique, cela implique que",
            "Ces résultats indiquent que",
            "Nous estimons que",
        ),
    },
    "pt": {
        "result": (
            "Nossos resultados mostram que",
            "Constatamos que",
            "Verifica-se que",
            "Nossa análise indica que",
            "Esses dados sugerem que",
            "Observamos que",
            "No conjunto, constatamos que",
            "Em suma, nossas observações mostram que",
            "Como esperado, constatamos que",
            "Cabe notar que",
            "Essas análises revelam que",
            "Nossas estimativas indicam que",
            "Chama a atenção que",
            "Na maioria dos casos, constatamos que",
        ),
        "implication": (
            "Esses resultados sugerem que",
            "Nossos resultados indicam que",
            "Concluímos que",
            "Decorre daí que",
            "Defendemos que",
            "Isso sugere que",
            "Na prática, isso significa que",
            "Esses resultados apontam que",
        ),
    },
}

TEMPLATES: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {
    ("en", "natural"): {
        "context": (
            "An understanding of {T} is essential to anticipate the effects of {D}.",
            "In recent years, concern for {T} has grown {S}.",
            "In coastal and marine science, the coupling of {T} with {T2} is a central question.",
            "In many settings, the variability observed {S} is largely linked to {T}.",
            "It remains a challenge to make predictions of {T}, in particular in the context of {D}.",
            "As recent work has emphasised, the role of {T} in the dynamics of {T2} is substantial.",
            "With the recognition of {D} as a threat, research on {T} has grown steadily.",
            "Of the processes at work {S}, few are as poorly understood as {T}.",
            "It is now clear that the state of {T} is sensitive to {D}.",
            "For decades, research on {T} has ignored its links to {T2}.",
            "To managers as well as scientists, the question of {T} is of growing interest.",
            "In the last decade, new observations of {T} have become available {S}.",
        ),
        "gap": (
            "To date, the link of {T} to {T2} is poorly constrained.",
            "To our knowledge, few datasets on {T} are long enough to separate trends from natural variability.",
            "At present, existing approaches rarely account for {T2}, which limits their value for {T}.",
            "In particular, the influence of {T2} on {T} has seldom been quantified.",
            "It is unclear to what extent the dynamics of {T} are controlled by {T2}.",
            "As a consequence, estimates of {T} are uncertain.",
            "Such gaps limit our ability to anticipate changes in {T}.",
            "No consensus has yet emerged on the mechanisms of control of {T}.",
        ),
        "aim": (
            "In this work, we rely on the combination of {M} with {M2} for a quantification of {T} {S}.",
            "In this paper, we rely on {M} to examine the response of {T} to {D}.",
            "We develop a framework for the link of {T} to {T2}, which we test against observations.",
            "This paper is an investigation of {T} with {M} at {N} sites.",
            "We assess the sensitivity of {T} to {T2} with {M}.",
            "Our aim is to disentangle the effects of {T2} from those of {D} on {T}.",
            "To this end, a new dataset on {T} {S} is analysed with {M}.",
            "We present the first estimates of {T} for {N} sites {S}.",
            "This study relies on {M} as well as on {M2} for a map of {T} {S}.",
            "Our objective is a better description of {T} and of its links with {T2}.",
        ),
        "data": (
            "Our analysis draws on observations collected between {Y1} and {Y2} at {N} stations.",
            "We complemented observations from {N} monitoring stations with {M}.",
            "Our dataset is a combination of {M} with {M2} for the period {Y1}–{Y2}.",
            "In total, {N} field campaigns were carried out between {Y1} and {Y2}.",
            "These data cover the period from {Y1} to {Y2}.",
            "We used {N} sampling sites, visited every month from {Y1} to {Y2}.",
            "These records were checked for quality with {M}.",
            "Our model is forced by observations from {Y1} to {Y2}.",
        ),
        "result": (
            "{T} increased markedly in extreme events, while no significant change in {T2} was detected",
            "about {P}% of the observed variance in {T} is explained by {T2}",
            "a strong seasonal signal in {T} is modulated by {T2}",
            "the response of {T} to {D} is highly variable from one site to another",
            "simulations reproduce the observed spatial structure of {T} with good skill",
            "spatial patterns of {T} are closely associated with {T2}",
            "the largest changes in {T} occur {S}",
            "differences in {T} between sites are mainly driven by {T2}",
            "no significant trend in {T} is detectable for the study period",
            "the dependence of {T} on {D} is nonlinear",
            "most of the variability in {T} is due to {T2}",
            "the effect of {T2} on {T} is strongest {S}",
            "the relation of {T} to {T2} has shifted since the early 2000s",
            "estimates of {T} are sensitive to the choice of {M}",
            "the contribution of {T2} to {T} is larger than previously thought",
            "a clear link of {T} to {T2} is found in {N} of the sites",
        ),
        "implication": (
            "more attention to {T} is needed in assessments of {T2}",
            "the monitoring of {T} should be extended to other settings",
            "management of {T} can benefit from {M}",
            "the integration of {T} in models of {T2} is a priority",
            "long-term observations of {T} are essential in the context of {D}",
            "the role of {T} for {T2} has been underestimated",
            "the approach can be transferred to the study of {T} in other regions",
            "predictions of {T} in the context of {D} are uncertain",
        ),
        "title": (
            "{^T} {S}: insights from {M}",
            "{^T} {S}: the role of {T2}",
            "On the link of {T} to {T2} {S}",
            "A quantification of {T} with {M}",
            "Drivers of {T} {S}",
            "Long-term changes in {T} {S}",
            "Towards an integrated view of {T} in relation to {T2}",
            "An assessment of {T} {S} with {M}",
            "The role of {T2} in {T}",
            "New estimates of {T} {S}",
            "Controls on {T} {S}",
            "{^T} under {D}: a case {S}",
            "What drives {T}? Evidence from {N} sites",
        ),
    },
    ("en", "social"): {
        "context": (
            "In the past two decades, debates on {T} have intensified {S}.",
            "In many places, public action on {T} is increasingly tied to {T2}.",
            "For coastal territories exposed to {D}, the question of {T} has become central.",
            "Research on {T} has long overlooked the role of {T2}.",
            "In coastal societies, the place of {T} has changed profoundly since the 1980s.",
            "It is often in conflicts over {T} that coastal futures are negotiated.",
            "Of the issues at stake {S}, few are as contested as {T}.",
            "As coastal pressures rise, the regulation of {T} is under scrutiny.",
        ),
        "gap": (
            "It remains unclear what the everyday experience of {T} is for the people most directly concerned.",
            "To date, the connection of {T} with {T2} has rarely been studied.",
            "Such questions have seldom been addressed from the point of view of local actors.",
            "In the literature, the analysis of {T} is rarely linked to {T2}.",
            "Its political dimension, in particular, has been neglected.",
            "To date, little attention has been paid to the history of {T}.",
        ),
        "aim": (
            "This article is an examination of {T} {S}, based on {M}.",
            "We analyse the negotiations on {T} between local actors, public authorities and users of the coast.",
            "This paper relies on a combination of {M} with {M2} to trace the trajectories of {T} {S}.",
            "Our aim is to understand how the question of {T} is framed by different actors.",
            "In this article, we follow the controversies on {T} {S}.",
            "We explore the relations of {T} with {T2} by means of {M}.",
            "This paper asks who benefits from {T} and who bears its costs.",
            "We compare {N} cases of {T} {S}.",
        ),
        "data": (
            "Our analysis is based on {N} interviews, field observations and archival documents.",
            "We collected material between {Y1} and {Y2} in {N} coastal municipalities.",
            "Our corpus combines planning documents, press articles and {N} interviews conducted between {Y1} and {Y2}.",
            "In total, {N} people were interviewed between {Y1} and {Y2}.",
            "We also analysed {N} sets of municipal council minutes.",
            "These sources were coded with {M}.",
        ),
        "result": (
            "the trajectories of {T} are closely tied to {T2} as well as to old tensions on the coast",
            "views of {T} differ between residents and elected officials",
            "the cases differ sharply in the importance they give to {T}",
            "local arrangements for {T} are often at odds with national frameworks",
            "the place of {T} in local debates is growing",
            "public action on {T} is shaped by {T2}",
            "conflicts over {T} are rooted in {T2}",
            "the framing of {T} in the press has changed over the period",
            "the voice of fishers in debates on {T} is weak",
            "the regulation of {T} relies on informal arrangements",
            "the history of {T} is key to an understanding of {T2}",
            "the governance of {T} remains highly fragmented",
        ),
        "implication": (
            "policies on {T} should pay more attention to {T2}",
            "the governance of {T} is in need of new forms of participation",
            "debates on {T} gain from a closer look at {T2}",
            "the question of {T} is also a question of justice",
            "comparative work on {T} is needed",
            "public action on {T} in the face of {D} needs to be rethought",
        ),
        "title": (
            "Negotiating {T} {S}",
            "Rethinking {T} in the face of {D}",
            "{^T} and {T2}: evidence from {N} coastal municipalities",
            "Who governs {T}? Actors, arenas and conflicts {S}",
            "{^T} {S}: a comparative perspective",
            "The politics of {T} {S}",
            "Living with {T}: an ethnography {S}",
            "{^T} as a public problem",
        ),
    },
    ("fr", "natural"): {
        "context": (
            "La compréhension {de:T} est essentielle pour anticiper les effets {de:D}.",
            "La question {de:T} se pose avec une acuité croissante {S}.",
            "Le couplage entre {T} et {T2} est une question centrale pour les sciences de la mer et du littoral.",
            "La variabilité observée {S} est en grande partie liée {a:T}.",
            "La prévision {de:T} est difficile, en particulier dans le contexte {de:D}.",
            "Des travaux récents ont souligné le rôle {de:T} et son effet sur l'évolution {de:T2}.",
            "On note que l'intérêt porté {a:T} est en forte hausse depuis la prise en compte {de:D}.",
            "Il existe peu de processus aussi mal connus {S} que {T}.",
            "Il est désormais établi que l'état {de:T} est sensible {a:D}.",
            "Depuis plusieurs décennies, les recherches sur {T} ont souvent négligé ses liens avec {T2}.",
            "La question {de:T} est aujourd'hui d'un intérêt croissant pour les gestionnaires comme pour les scientifiques.",
            "Au cours de la dernière décennie, de nouvelles observations {de:T} sont devenues disponibles {S}.",
        ),
        "gap": (
            "En revanche, le lien entre {T} et {T2} est mal contraint.",
            "Or peu de jeux de données sur {T} sont assez longs pour distinguer les tendances de la variabilité naturelle.",
            "En outre, la prise en compte {de:T2} est rare dans les approches existantes, et cela limite leur portée pour {T}.",
            "En particulier, l'influence {de:T2} sur {T} est rarement quantifiée.",
            "On ignore encore dans quelle mesure la dynamique {de:T} est contrôlée par {T2}.",
            "De ce fait, les estimations {de:T} sont incertaines.",
            "Ces lacunes limitent notre capacité à anticiper l'évolution {de:T}.",
            "Il n'existe pas de consensus sur les mécanismes de contrôle {de:T}.",
        ),
        "aim": (
            "Nous combinons ici {M} et {M2} en vue de quantifier {T} {S}.",
            "Ce travail mobilise {M} en vue d'examiner la réponse {de:T} {a:D}.",
            "Nous proposons un cadre reliant {T} {a:T2}, que nous confrontons aux observations.",
            "Cet article analyse {T} en s'appuyant sur {M} et sur {N} sites.",
            "Nous évaluons, à l'aide {de:M}, la sensibilité {de:T} {a:T2}.",
            "Notre objectif est de distinguer les effets {de:T2} de ceux {de:D} sur {T}.",
            "Un nouveau jeu de données sur {T} {S} est analysé à l'aide {de:M}.",
            "Nous présentons les premières estimations {de:T} pour {N} sites {S}.",
            "Cette étude s'appuie sur {M} ainsi que sur {M2} pour cartographier {T} {S}.",
            "Notre objectif est une meilleure description {de:T} et de ses liens avec {T2}.",
        ),
        "data": (
            "Notre analyse s'appuie sur des observations acquises entre {Y1} et {Y2} sur {N} stations.",
            "Nous complétons les observations issues de {N} stations de suivi par {M}.",
            "Le jeu de données associe {M} et {M2} de {Y1} à {Y2}.",
            "Au total, {N} campagnes de terrain ont été menées entre {Y1} et {Y2}.",
            "Ces données couvrent la période de {Y1} à {Y2}.",
            "Nous avons suivi {N} sites d'échantillonnage chaque mois de {Y1} à {Y2}.",
            "Ces enregistrements ont été contrôlés à l'aide {de:M}.",
            "Le modèle est forcé par des observations de {Y1} à {Y2}.",
        ),
        "result": (
            "l'intensité {de:T} augmente nettement en cas d'événement extrême, alors qu'aucune évolution significative {de:T2} n'est détectée",
            "environ {P} % de la variance observée {de:T} est expliquée par {T2}",
            "un signal saisonnier marqué {de:T} est modulé par {T2}",
            "la réponse {de:T} {a:D} est très variable d'un site à l'autre",
            "les simulations reproduisent correctement la structure spatiale {de:T}",
            "la distribution spatiale {de:T} est étroitement associée {a:T2}",
            "les changements les plus marqués {de:T} se produisent {S}",
            "les différences {de:T} d'un site à l'autre s'expliquent principalement par {T2}",
            "aucune tendance significative {de:T} n'est détectable au cours de la période étudiée",
            "la dépendance {de:T} {a:D} est non linéaire",
            "l'essentiel de la variabilité {de:T} est dû {a:T2}",
            "l'effet {de:T2} sur {T} est maximal {S}",
            "la relation entre {T} et {T2} a changé depuis le début des années 2000",
            "les estimations {de:T} sont sensibles au choix {de:M}",
            "la contribution {de:T2} {a:T} est plus importante qu'on ne le pensait",
            "un lien net entre {T} et {T2} est observé dans {N} des sites",
        ),
        "implication": (
            "une attention accrue {a:T} est nécessaire dans l'évaluation {de:T2}",
            "le suivi {de:T} devrait être étendu à d'autres sites",
            "la gestion {de:T} peut tirer parti {de:M}",
            "l'intégration {de:T} dans les modèles {de:T2} constitue une priorité",
            "des observations à long terme {de:T} sont indispensables dans le contexte {de:D}",
            "le rôle {de:T} pour {T2} a été sous-estimé",
            "cette approche est transposable à l'étude {de:T} dans d'autres régions",
            "les prévisions {de:T} dans le contexte {de:D} restent incertaines",
        ),
        "title": (
            "{^T} {S} : apports {de:M}",
            "{^T} et {T2} {S}",
            "Relier {T} {a:T2} {S}",
            "Quantifier {T} par {M}",
            "Les facteurs de contrôle {de:T} {S}",
            "Évolution à long terme {de:T} {S}",
            "Vers une approche intégrée {de:T} et {de:T2}",
            "Évaluer {T} {S} à l'aide {de:M}",
            "Le rôle {de:T2} dans la dynamique {de:T}",
            "{^T} face {a:D}",
            "Nouvelles estimations {de:T} {S}",
            "Les mécanismes de contrôle {de:T} {S}",
            "Qu'est-ce qui contrôle {T} ? Apports de {N} sites",
        ),
    },
    ("fr", "social"): {
        "context": (
            "Ces vingt dernières années, les débats relatifs {a:T} se sont intensifiés {S}.",
            "Aujourd'hui, l'action publique relative {a:T} est de plus en plus liée {a:T2}.",
            "La question {de:T} est devenue centrale pour les territoires littoraux confrontés {a:D}.",
            "Les recherches sur {T} ont longtemps négligé le rôle {de:T2}.",
            "Au sein des sociétés littorales, la place {de:T} a profondément changé depuis les années 1980.",
            "C'est souvent dans les conflits autour {de:T} que se négocie l'avenir du littoral.",
            "Il existe peu d'enjeux aussi disputés {S} que {T}.",
            "La régulation {de:T} fait l'objet d'une attention nouvelle face à la montée des pressions littorales.",
        ),
        "gap": (
            "On sait pourtant peu de choses de l'expérience quotidienne {de:T} et de la manière dont les personnes concernées la vivent.",
            "Les travaux existants relient rarement {T} {a:T2}.",
            "Ces questions ont rarement été abordées du point de vue des acteurs locaux.",
            "La littérature relie rarement l'analyse {de:T} {a:T2}.",
            "Sa dimension politique, en particulier, a été négligée.",
            "On a jusqu'ici porté peu d'attention à l'histoire {de:T}.",
        ),
        "aim": (
            "Cet article examine {T} {S} à partir {de:M}.",
            "Nous analysons les négociations autour {de:T} et le rôle des acteurs locaux, des pouvoirs publics et des usagers du littoral.",
            "Cet article combine {M} et {M2} en vue de retracer les trajectoires {de:T} {S}.",
            "Notre objectif est de comprendre comment la question {de:T} est construite par différents acteurs.",
            "Nous suivons ici les controverses relatives {a:T} {S}.",
            "Nous explorons les relations entre {T} et {T2} à l'aide {de:M}.",
            "Cet article s'interroge sur les bénéficiaires {de:T} et sur ceux qui en supportent les coûts.",
            "Nous comparons {N} situations relatives {a:T} {S}.",
        ),
        "data": (
            "Notre analyse repose sur {N} entretiens, des observations de terrain et des documents d'archives.",
            "Le matériau a été recueilli entre {Y1} et {Y2} dans {N} communes littorales.",
            "Le corpus associe des documents de planification, des articles de presse et {N} entretiens menés entre {Y1} et {Y2}.",
            "Au total, {N} personnes ont été interrogées entre {Y1} et {Y2}.",
            "Nous avons également analysé {N} comptes rendus de conseils municipaux.",
            "Ces sources ont été codées à l'aide {de:M}.",
        ),
        "result": (
            "les trajectoires {de:T} sont étroitement liées {a:T2} et à des tensions anciennes autour de l'espace littoral",
            "les habitants et les élus ont des représentations contrastées {de:T}",
            "les cas étudiés diffèrent nettement par l'importance accordée {a:T}",
            "les arrangements locaux relatifs {a:T} sont souvent éloignés des cadres nationaux",
            "la place {de:T} est croissante dans les débats locaux",
            "l'action publique relative {a:T} est façonnée par {T2}",
            "les conflits autour {de:T} prennent racine dans {T2}",
            "le cadrage médiatique {de:T} a changé au cours de la période",
            "la voix des pêcheurs dans les débats sur {T} reste faible",
            "la régulation {de:T} repose sur des arrangements informels",
            "l'histoire {de:T} est essentielle à la compréhension {de:T2}",
            "la gouvernance {de:T} reste très fragmentée",
        ),
        "implication": (
            "les politiques relatives {a:T} devraient accorder plus d'attention {a:T2}",
            "la gouvernance {de:T} appelle de nouvelles formes de participation",
            "les débats sur {T} gagneraient à mieux prendre en compte {T2}",
            "la question {de:T} est aussi une question de justice",
            "des travaux comparatifs sur {T} sont nécessaires",
            "l'action publique relative {a:T} face {a:D} doit être repensée",
        ),
        "title": (
            "Négocier {T} {S}",
            "Repenser {T} face {a:D}",
            "{^T} et {T2} : enquête dans {N} communes littorales",
            "Qui gouverne {T} ? Acteurs, arènes et conflits {S}",
            "{^T} {S} : une perspective comparée",
            "La politique {de:T} {S}",
            "Vivre avec {T} : une ethnographie {S}",
            "{^T}, un problème public",
        ),
    },
}

TEMPLATES[("pt", "natural")] = {
    "context": (
        "A compreensão {de:T} é essencial para antecipar os efeitos {de:D}.",
        "A questão {de:T} se coloca com acuidade crescente {S}.",
        "O acoplamento entre {T} e {T2} é uma questão central para as ciências do mar e da zona costeira.",
        "A variabilidade observada {S} está em grande parte ligada {a:T}.",
        "A previsão {de:T} é difícil, em particular no contexto {de:D}.",
        "Trabalhos recentes destacaram o papel {de:T} e seu efeito sobre a evolução {de:T2}.",
        "Nota-se que o interesse {por:T} cresce fortemente desde que se passou a considerar {D}.",
        "Poucos processos são tão mal conhecidos {S} quanto {T}.",
        "Está hoje estabelecido que o estado {de:T} é sensível {a:D}.",
        "Há várias décadas, as pesquisas sobre {T} frequentemente negligenciaram suas relações com {T2}.",
        "A questão {de:T} desperta hoje um interesse crescente entre gestores e cientistas.",
        "Na última década, novas observações {de:T} tornaram-se disponíveis {S}.",
    ),
    "gap": (
        "Em contrapartida, a relação entre {T} e {T2} é mal delimitada.",
        "Ora, poucos conjuntos de dados sobre {T} são longos o suficiente para distinguir as tendências da variabilidade natural.",
        "Além disso, a consideração {de:T2} é rara nas abordagens existentes, o que limita seu alcance para {T}.",
        "Em particular, a influência {de:T2} sobre {T} raramente é quantificada.",
        "Ainda não se sabe em que medida a dinâmica {de:T} é controlada {por:T2}.",
        "Por isso, as estimativas {de:T} são incertas.",
        "Essas lacunas limitam nossa capacidade de antecipar a evolução {de:T}.",
        "Não há consenso sobre os mecanismos de controle {de:T}.",
    ),
    "aim": (
        "Combinamos aqui {M} e {M2} com o objetivo de quantificar {T} {S}.",
        "Este trabalho mobiliza {M} para examinar a resposta {de:T} {a:D}.",
        "Propomos um arcabouço que relaciona {T} {a:T2}, que confrontamos com as observações.",
        "Este artigo analisa {T} com base {em:M} e em {N} sítios.",
        "Avaliamos, por meio {de:M}, a sensibilidade {de:T} {a:T2}.",
        "Nosso objetivo é distinguir os efeitos {de:T2} daqueles {de:D} sobre {T}.",
        "Um novo conjunto de dados sobre {T} {S} é analisado por meio {de:M}.",
        "Apresentamos as primeiras estimativas {de:T} para {N} sítios {S}.",
        "Este estudo se apoia {em:M} e também {em:M2} para mapear {T} {S}.",
        "Nosso objetivo é uma melhor descrição {de:T} e de suas relações com {T2}.",
    ),
    "data": (
        "Nossa análise se baseia em observações obtidas entre {Y1} e {Y2} em {N} estações.",
        "Complementamos as observações de {N} estações de monitoramento com {M}.",
        "O conjunto de dados associa {M} e {M2} de {Y1} a {Y2}.",
        "No total, {N} campanhas de campo foram realizadas entre {Y1} e {Y2}.",
        "Esses dados cobrem o período de {Y1} a {Y2}.",
        "Acompanhamos {N} pontos de amostragem todos os meses de {Y1} a {Y2}.",
        "Esses registros foram controlados por meio {de:M}.",
        "O modelo é forçado por observações de {Y1} a {Y2}.",
    ),
    "result": (
        "a intensidade {de:T} aumenta nitidamente em caso de evento extremo, ao passo que nenhuma evolução significativa {de:T2} é detectada",
        "cerca de {P}% da variância observada {de:T} é explicada {por:T2}",
        "um sinal sazonal marcado {de:T} é modulado {por:T2}",
        "a resposta {de:T} {a:D} é muito variável de um sítio para outro",
        "as simulações reproduzem corretamente a estrutura espacial {de:T}",
        "a distribuição espacial {de:T} está estreitamente associada {a:T2}",
        "as mudanças mais marcadas {de:T} ocorrem {S}",
        "as diferenças {de:T} de um sítio para outro se explicam principalmente {por:T2}",
        "nenhuma tendência significativa {de:T} é detectável ao longo do período estudado",
        "a dependência {de:T} em relação {a:D} é não linear",
        "a maior parte da variabilidade {de:T} se deve {a:T2}",
        "o efeito {de:T2} sobre {T} é máximo {S}",
        "a relação entre {T} e {T2} mudou desde o início dos anos 2000",
        "as estimativas {de:T} são sensíveis à escolha {de:M}",
        "a contribuição {de:T2} para {T} é maior do que se pensava",
        "uma relação clara entre {T} e {T2} é observada em {N} dos sítios",
    ),
    "implication": (
        "é necessária uma atenção maior {a:T} na avaliação {de:T2}",
        "o monitoramento {de:T} deveria ser estendido a outros sítios",
        "a gestão {de:T} pode se beneficiar {de:M}",
        "a integração {de:T} nos modelos {de:T2} constitui uma prioridade",
        "observações de longo prazo {de:T} são indispensáveis no contexto {de:D}",
        "o papel {de:T} para {T2} foi subestimado",
        "essa abordagem é transponível ao estudo {de:T} em outras regiões",
        "as previsões {de:T} no contexto {de:D} continuam incertas",
    ),
    "title": (
        "{^T} {S}: contribuições {de:M}",
        "{^T} e {T2} {S}",
        "Relacionar {T} {a:T2} {S}",
        "Quantificar {T} por meio {de:M}",
        "Os fatores de controle {de:T} {S}",
        "Evolução de longo prazo {de:T} {S}",
        "Por uma abordagem integrada {de:T} e {de:T2}",
        "Avaliar {T} {S} por meio {de:M}",
        "O papel {de:T2} na dinâmica {de:T}",
        "{^T} diante {de:D}",
        "Novas estimativas {de:T} {S}",
        "Os mecanismos de controle {de:T} {S}",
        "O que controla {T}? Contribuições de {N} sítios",
    ),
}
TEMPLATES[("pt", "social")] = {
    "context": (
        "Nos últimos vinte anos, os debates relativos {a:T} se intensificaram {S}.",
        "Hoje, a ação pública relativa {a:T} está cada vez mais ligada {a:T2}.",
        "A questão {de:T} tornou-se central para os territórios costeiros expostos {a:D}.",
        "As pesquisas sobre {T} por muito tempo negligenciaram o papel {de:T2}.",
        "Nas sociedades litorâneas, o lugar {de:T} mudou profundamente desde os anos 1980.",
        "É muitas vezes nos conflitos em torno {de:T} que se negocia o futuro do litoral.",
        "Poucos temas são tão disputados {S} quanto {T}.",
        "A regulação {de:T} é objeto de nova atenção diante da crescente pressão sobre o litoral.",
    ),
    "gap": (
        "Sabe-se, contudo, pouco sobre a experiência cotidiana {de:T} e sobre a maneira como as pessoas envolvidas a vivem.",
        "Os trabalhos existentes raramente relacionam {T} {a:T2}.",
        "Essas questões raramente foram abordadas do ponto de vista dos atores locais.",
        "A literatura raramente relaciona a análise {de:T} {a:T2}.",
        "Sua dimensão política, em particular, foi negligenciada.",
        "Até agora, deu-se pouca atenção à história {de:T}.",
    ),
    "aim": (
        "Este artigo examina {T} {S} a partir {de:M}.",
        "Analisamos as negociações em torno {de:T} e o papel dos atores locais, dos poderes públicos e dos usuários do litoral.",
        "Este artigo combina {M} e {M2} para reconstituir as trajetórias {de:T} {S}.",
        "Nosso objetivo é compreender como a questão {de:T} é construída por diferentes atores.",
        "Acompanhamos aqui as controvérsias relativas {a:T} {S}.",
        "Exploramos as relações entre {T} e {T2} por meio {de:M}.",
        "Este artigo se interroga sobre os beneficiários {de:T} e sobre aqueles que arcam com seus custos.",
        "Comparamos {N} situações relativas {a:T} {S}.",
    ),
    "data": (
        "Nossa análise se baseia em {N} entrevistas, observações de campo e documentos de arquivo.",
        "O material foi coletado entre {Y1} e {Y2} em {N} municípios costeiros.",
        "O corpus reúne documentos de planejamento, matérias de imprensa e {N} entrevistas realizadas entre {Y1} e {Y2}.",
        "No total, {N} pessoas foram entrevistadas entre {Y1} e {Y2}.",
        "Analisamos também {N} atas de câmaras municipais.",
        "Essas fontes foram codificadas por meio {de:M}.",
    ),
    "result": (
        "as trajetórias {de:T} estão estreitamente ligadas {a:T2} e a tensões antigas em torno do espaço litorâneo",
        "moradores e eleitos têm representações contrastantes {de:T}",
        "os casos estudados diferem nitidamente pela importância atribuída {a:T}",
        "os arranjos locais relativos {a:T} estão muitas vezes distantes dos marcos nacionais",
        "o lugar {de:T} é crescente nos debates locais",
        "a ação pública relativa {a:T} é moldada {por:T2}",
        "os conflitos em torno {de:T} têm raízes {em:T2}",
        "o enquadramento midiático {de:T} mudou ao longo do período",
        "a voz dos pescadores nos debates sobre {T} continua fraca",
        "a regulação {de:T} se apoia em arranjos informais",
        "a história {de:T} é essencial para a compreensão {de:T2}",
        "a governança {de:T} continua muito fragmentada",
    ),
    "implication": (
        "as políticas relativas {a:T} deveriam dar mais atenção {a:T2}",
        "a governança {de:T} exige novas formas de participação",
        "os debates sobre {T} ganhariam ao considerar melhor {T2}",
        "a questão {de:T} é também uma questão de justiça",
        "trabalhos comparativos sobre {T} são necessários",
        "a ação pública relativa {a:T} diante {de:D} precisa ser repensada",
    ),
    "title": (
        "Negociar {T} {S}",
        "Repensar {T} diante {de:D}",
        "{^T} e {T2}: pesquisa em {N} municípios costeiros",
        "Quem governa {T}? Atores, arenas e conflitos {S}",
        "{^T} {S}: uma perspectiva comparada",
        "A política {de:T} {S}",
        "Viver com {T}: uma etnografia {S}",
        "{^T}, um problema público",
    ),
}

_ELISION = re.compile(r"\bque (?=[aeiouyâàéèêëîïôû])", re.IGNORECASE)


def elide(text: str) -> str:
    """French elision of *que* before a vowel (``que aucune`` → ``qu'aucune``)."""
    return _ELISION.sub("qu'", text)


_SLOT_RE = re.compile(r"\{(\^?)(?:(de|a|em|por):)?([A-Z][A-Z0-9]*)\}")


def capitalise(text: str) -> str:
    """Upper-case the first letter, unless the first word already has capitals."""
    if not text:
        return text
    first_word = text.split(" ", 1)[0]
    if any(c.isupper() for c in first_word):
        return text
    return text[0].upper() + text[1:]


def render(template: str, slots: dict[str, object], language: str) -> str:
    """Fill *template* with *slots* (terms, settings or strings) in *language*."""

    def one(match: re.Match[str]) -> str:
        cap, form, name = match.group(1), match.group(2), match.group(3)
        value = slots[name]
        if isinstance(value, Term):
            if language == "en":
                text = value.en
            elif language == "pt":
                text = {
                    None: value.pt_def,
                    "de": value.pt_de,
                    "a": value.pt_a,
                    "em": value.pt_em,
                    "por": value.pt_por,
                }[form]
            else:
                text = {None: value.fr_def, "de": value.fr_de, "a": value.fr_a}[form]
        elif isinstance(value, Setting):
            text = {"en": value.en, "fr": value.fr, "pt": value.pt}[language]
        else:
            text = str(value)
        return capitalise(text) if cap else text

    return _SLOT_RE.sub(one, template)


def slot_names(template: str) -> set[str]:
    """The slot names a template uses."""
    return {m.group(3) for m in _SLOT_RE.finditer(template)}


def slot_free_pieces(template: str) -> list[str]:
    """The literal pieces of a template between its slots (stripped, with a letter)."""
    pieces = (p.strip() for p in _SLOT_RE.split(template)[::4])
    return [p for p in pieces if any(c.isalpha() for c in p)]


def weighted_sample(rng: random.Random, items: Sequence, weights: Sequence[float], k: int) -> list:
    """Draw up to *k* distinct items, each draw proportional to the remaining weights."""
    pool = list(range(len(items)))
    w = [float(x) for x in weights]
    out = []
    for _ in range(min(k, len(pool))):
        total = sum(w[i] for i in pool)
        r = rng.random() * total
        acc = 0.0
        pick = pool[-1]
        for i in pool:
            acc += w[i]
            if r < acc:
                pick = i
                break
        out.append(items[pick])
        pool.remove(pick)
    return out


@dataclass(frozen=True)
class TextPlan:
    """What a work is about: everything the composer needs besides randomness."""

    language: str  # "en", "fr" or "pt"
    kind: str  # "natural" or "social"
    year: int
    primary: Theme
    primary_weights: tuple[float, ...]  # author's preference over primary.terms
    secondary: Theme | None
    secondary_weights: tuple[float, ...]  # same, over secondary.terms
    methods: tuple[Method, ...]  # the author's preferred methods
    settings: tuple[Setting, ...]  # typical settings of the author's group
    drivers: tuple[Term, ...]
    all_methods: tuple[Method, ...]
    all_settings: tuple[Setting, ...]


# A theme with fewer topic terms than this (one that is mostly techniques)
# takes its topics from the work's secondary theme.
MIN_TOPICS = 12
# How many focal terms a work draws (main theme, other theme, techniques), and
# how often a topic slot reuses a focal term rather than drawing a fresh one.
FOCAL_TOPICS = 5
FOCAL_SECONDARY = 3
FOCAL_TECHNIQUES = 3
P_FOCAL = 0.5


def _part(theme: Theme, weights: Sequence[float], technique: bool) -> tuple[list, list]:
    """The theme's topics (or techniques) and their weights."""
    pairs = [(t, w) for t, w in zip(theme.terms, weights, strict=True) if t.technique == technique]
    return [t for t, _ in pairs], [w for _, w in pairs]


class _Filler:
    """Draws slot values for one work, favouring a few focal terms."""

    def __init__(self, rng: random.Random, plan: TextPlan) -> None:
        self.rng = rng
        self.plan = plan
        main, main_w = plan.primary, plan.primary_weights
        other, other_w = plan.secondary, plan.secondary_weights
        if len(main.topics) < MIN_TOPICS and other is not None:
            main, main_w, other, other_w = other, other_w, main, main_w
        self.topics, self.topic_w = _part(main, main_w, technique=False)
        self.focal = weighted_sample(rng, self.topics, self.topic_w, FOCAL_TOPICS)
        self.focal2: list[Term] = []
        if other is not None:
            topics2, topics2_w = _part(other, other_w, technique=False)
            self.focal2 = weighted_sample(rng, topics2, topics2_w, FOCAL_SECONDARY)
        techniques, tech_w = _part(plan.primary, plan.primary_weights, technique=True)
        self.focal_tech = weighted_sample(rng, techniques, tech_w, FOCAL_TECHNIQUES)
        self.p_tech = min(0.7, 0.15 + len(techniques) / 40) if techniques else 0.0
        kinds = ("any", plan.kind)
        self.method_pool = [m.term for m in plan.methods if m.kind in kinds] or [
            m.term for m in plan.all_methods if m.kind in kinds
        ]
        self.any_methods = [m.term for m in plan.all_methods if m.kind in kinds]
        # One study setting per work, often one of the group's usual settings.
        settings_ok = [s for s in plan.all_settings if plan.kind == "natural" or s.social]
        preferred = [s for s in plan.settings if s in settings_ok]
        pool = preferred if preferred and rng.random() < 0.5 else settings_ok
        self.site = rng.choice(pool)
        self.settings_all = settings_ok

    def term(self) -> Term:
        if self.rng.random() < P_FOCAL:
            return self.rng.choice(self.focal)
        return weighted_sample(self.rng, self.topics, self.topic_w, 1)[0]

    def term2(self, avoid: Term) -> Term:
        for _ in range(20):
            if self.focal2 and self.rng.random() < 0.6:
                cand = self.rng.choice(self.focal2)
            elif self.rng.random() < 0.5:
                cand = self.rng.choice(self.focal)
            else:
                cand = weighted_sample(self.rng, self.topics, self.topic_w, 1)[0]
            if cand != avoid:
                return cand
        return next(t for t in self.topics if t != avoid)

    def method(self, avoid: Term | None = None) -> Term:
        for _ in range(20):
            r = self.rng.random()
            if self.focal_tech and r < self.p_tech:
                cand = self.rng.choice(self.focal_tech)
            elif r < self.p_tech + (1.0 - self.p_tech) * 0.8:
                cand = self.rng.choice(self.method_pool)
            else:
                cand = self.rng.choice(self.any_methods)
            if cand != avoid:
                return cand
        return next(m for m in self.any_methods if m != avoid)

    def setting(self) -> Setting:
        if self.rng.random() < 0.85:
            return self.site
        return self.rng.choice(self.settings_all)

    def slots(self, names: set[str], *, headline: bool = False) -> dict[str, object]:
        """Values for the slots in *names*; a *headline* uses the work's focal terms."""
        rng = self.rng
        out: dict[str, object] = {}
        t = self.focal[0] if headline else self.term()
        out["T"] = t
        if "T2" in names:
            if headline and (self.focal2 or len(self.focal) > 1):
                out["T2"] = self.focal2[0] if self.focal2 else self.focal[1]
            else:
                out["T2"] = self.term2(t)
        m = self.focal_tech[0] if headline and self.focal_tech else self.method()
        out["M"] = m
        if "M2" in names:
            out["M2"] = self.method(avoid=m)
        out["S"] = self.setting()
        out["D"] = rng.choice(self.plan.drivers)
        out["N"] = str(rng.randint(6, 48))
        out["P"] = str(rng.randint(20, 85))
        y2 = self.plan.year - rng.randint(1, 2)
        out["Y1"] = str(y2 - rng.randint(3, 14))
        out["Y2"] = str(y2)
        return out


def compose(rng: random.Random, plan: TextPlan) -> tuple[str, str]:
    """Return ``(title, abstract)`` for one work; the abstract has 120–250 words."""
    templates = TEMPLATES[(plan.language, plan.kind)]
    filler = _Filler(rng, plan)

    leadins = {role: list(LEADINS[plan.language][role]) for role in CLAUSE_ROLES}
    for role in CLAUSE_ROLES:
        rng.shuffle(leadins[role])

    def sentence(template: str, headline: bool = False, role: str = "") -> str:
        slots = filler.slots(slot_names(template), headline=headline)
        text = render(template, slots, plan.language)
        if role in CLAUSE_ROLES:
            pool = leadins[role]
            text = f"{pool.pop() if pool else LEADINS[plan.language][role][0]} {text}."
        return elide(text) if plan.language == "fr" else text

    title = capitalise(sentence(rng.choice(templates["title"]), headline=True))

    unused = {role: list(templates[role]) for role in ROLES}
    for role in ROLES:
        rng.shuffle(unused[role])
    counts = {
        "context": rng.choice((1, 1, 2)),
        "gap": rng.choice((0, 1, 1)),
        "aim": 1,
        "data": rng.choice((0, 1)),
        "result": rng.choice((2, 2, 3)),
        "implication": rng.choice((1, 1, 2)),
    }
    chosen: list[tuple[int, str]] = []
    for idx, role in enumerate(ROLES):
        for _ in range(counts[role]):
            if unused[role]:
                chosen.append((idx, sentence(unused[role].pop(), role=role)))

    def words() -> int:
        return sum(len(s.split()) for _, s in chosen)

    target = rng.randint(MIN_WORDS + 15, MAX_WORDS - 15)
    extra_roles = ("result", "context", "data", "gap", "result", "implication")
    step = 0
    while words() < target and any(unused[r] for r in extra_roles):
        role = extra_roles[step % len(extra_roles)]
        step += 1
        if unused[role]:
            chosen.append((ROLES.index(role), sentence(unused[role].pop(), role=role)))
    # Trim from the optional roles if the last sentence overshot the ceiling.
    while words() > MAX_WORDS:
        for role in ("result", "context", "data", "gap", "implication"):
            idx = ROLES.index(role)
            same = [i for i, (r, _) in enumerate(chosen) if r == idx]
            if len(same) > 1 or (role in ("data", "gap") and same):
                del chosen[same[-1]]
                break
        else:  # pragma: no cover - templates are far below the ceiling
            break
    chosen.sort(key=lambda item: item[0])
    return title, " ".join(s for _, s in chosen)
