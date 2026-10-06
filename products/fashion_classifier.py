import re


MEN_TERMS = (
    'men', "men's", 'mens', 'male', 'boy', 'boys', 'agbada', 'senator wear',
    'kaftan', 'dashiki', 'fila cap', 'native wear for men', 'men shoes',
)
WOMEN_TERMS = (
    'women', "women's", 'womens', 'female', 'woman', 'girl', 'girls', 'bubu',
    'iro and buba', 'gele', 'aso ebi', 'wrapper', 'blouse', 'women shoes',
)
UNISEX_TERMS = (
    'unisex', 'gender neutral', 'gender-neutral', 'for all genders', 'all gender',
)


def _matches(text, terms):
    matches = []
    occupied_spans = []
    for term in sorted(terms, key=len, reverse=True):
        phrase_pattern = re.escape(term).replace(r'\ ', r'\s+')
        pattern = rf'(?<!\w){phrase_pattern}(?!\w)'
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if any(match.start() < end and match.end() > start for start, end in occupied_spans):
                continue
            matches.append(term)
            occupied_spans.append(match.span())
    return matches


def classify_fashion_section(name, description=''):
    """Return the deterministic section slug and an auditable explanation."""
    text = f'{name} {description}'.strip()
    explicit_unisex = _matches(text, UNISEX_TERMS)
    if explicit_unisex:
        return 'unisex', f"Explicit unisex wording matched: {', '.join(explicit_unisex)}."

    men_matches = _matches(text, MEN_TERMS)
    women_matches = _matches(text, WOMEN_TERMS)
    if not men_matches and not women_matches:
        return 'unisex', 'No gender-specific Fashion terms matched; assigned to Unisex.'
    if men_matches and not women_matches:
        return 'men', f"Men's Fashion terms matched: {', '.join(men_matches)}."
    if women_matches and not men_matches:
        return 'women', f"Women's Fashion terms matched: {', '.join(women_matches)}."

    difference = abs(len(men_matches) - len(women_matches))
    if difference >= 2:
        section = 'men' if len(men_matches) > len(women_matches) else 'women'
        matched = men_matches if section == 'men' else women_matches
        return section, f"Clear {section} term dominance ({len(matched)} matches); matched: {', '.join(matched)}."

    return 'unisex', (
        f"Mixed Fashion signals were not clearly dominant (men: {', '.join(men_matches)}; "
        f"women: {', '.join(women_matches)}); assigned to Unisex."
    )
