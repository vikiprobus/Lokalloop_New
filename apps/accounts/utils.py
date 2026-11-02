import re
import random

# Mapping of known multi-word or standard Indian cities to exact 3-letter city codes
KNOWN_CITY_CODES = {
    'chhatrapati sambhaji nagar': 'CSN',
    'sambhaji nagar': 'CSN',
    'navi mumbai': 'NMU',
    'new delhi': 'NDL',
    'pune': 'PUN',
    'akola': 'AKO',
    'mumbai': 'MUM',
    'nagpur': 'NAG',
    'nashik': 'NAS',
    'amravati': 'AMR',
    'kolhapur': 'KOL',
    'solapur': 'SOL',
    'thane': 'THA',
    'aurangabad': 'CSN',
    'bengaluru': 'BLR',
    'bangalore': 'BLR',
    'hyderabad': 'HYD',
    'chennai': 'CHE',
    'kolkata': 'KOL',
    'ahmedabad': 'AMD',
    'delhi': 'DEL',
    'jaipur': 'JAI',
    'indore': 'IND',
    'surat': 'SUR',
    'lucknow': 'LUK',
    'kanpur': 'KAN',
    'patna': 'PAT',
    'bhopal': 'BHO',
    'chandigarh': 'CHD',
}


def extract_city_code(city_name: str | None) -> str:
    """
    Extracts a 3-character uppercase city code adhering to:
    - Multi-Word Cities: First letter of each word in uppercase:
        * Chhatrapati Sambhaji Nagar -> CSN
        * Navi Mumbai -> NMU
        * New Delhi -> NDL
    - Single-Word Cities: First 3 letters in uppercase:
        * Pune -> PUN
        * Akola -> AKO
        * Mumbai -> MUM
        * Nagpur -> NAG
        * Nashik -> NAS
    - Fallback: Default to 'LOK' if city is blank.
    """
    if not city_name or not str(city_name).strip():
        return "LOK"

    normalized = str(city_name).strip()
    lowered = normalized.lower()

    if lowered in KNOWN_CITY_CODES:
        return KNOWN_CITY_CODES[lowered]

    # Clean non-alphanumeric characters except spaces
    cleaned = re.sub(r'[^A-Za-z\s]', ' ', normalized)
    words = [w for w in cleaned.split() if w]

    if not words:
        return "LOK"

    if len(words) == 1:
        code = words[0][:3].upper()
        return code.ljust(3, 'X')
    elif len(words) == 2:
        # e.g., Navi Mumbai -> N + MU = NMU
        w1, w2 = words[0], words[1]
        if len(w2) >= 2:
            code = (w1[0] + w2[:2]).upper()
        else:
            code = (w1[:2] + w2[0]).upper()
        return code[:3]
    else:
        # 3 or more words -> extract first letter of each of the first 3 words
        # e.g., Chhatrapati Sambhaji Nagar -> CSN
        code = "".join(w[0] for w in words[:3]).upper()
        return code.ljust(3, 'X')[:3]


def generate_unique_merchant_id(city_name: str | None) -> str:
    """
    Generates a collision-free merchant ID formatted as: [CITY_CODE]-[6_RANDOM_DIGITS]
    e.g. PUN-273849, CSN-472946, AKO-385959
    """
    from apps.accounts.models import SellerProfile

    city_code = extract_city_code(city_name)

    while True:
        digits = random.randint(100000, 999999)
        candidate = f"{city_code}-{digits}"
        if not SellerProfile.objects.filter(merchant_id=candidate).exists():
            return candidate
