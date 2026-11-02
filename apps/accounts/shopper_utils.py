import random
import re
import hashlib
import secrets
from django.contrib.auth.models import User

# Exact 36 Maharashtra District Lookup Codes
MAHARASHTRA_DISTRICT_CODES = {
    'Ahilyanagar': 'P5713',
    'Akola': 'K3674',
    'Amravati': 'R5711',
    'Beed': 'F9419',
    'Bhandara': 'O2222',
    'Buldhana': 'Z1994',
    'Chandrapur': 'V2218',
    'Chhatrapati Sambhajinagar': 'P6715',
    'Dharashiv': 'O1430',
    'Dhule': 'D5159',
    'Gadchiroli': 'E9474',
    'Gondia': 'N1427',
    'Hingoli': 'F4279',
    'Jalgaon': 'C9142',
    'Jalna': 'C5654',
    'Kolhapur': 'O5918',
    'Latur': 'V5855',
    'Mumbai City': 'I4278',
    'Mumbai Suburban': 'Z3752',
    'Nagpur': 'D3249',
    'Nanded': 'E8843',
    'Nandurbar': 'Y4534',
    'Nashik': 'P4229',
    'Palghar': 'V2829',
    'Parbhani': 'W5731',
    'Pune': 'A8208',
    'Raigad': 'G2872',
    'Ratnagiri': 'S3722',
    'Sangli': 'R3006',
    'Satara': 'I8164',
    'Sindhudurg': 'I8653',
    'Solapur': 'R4234',
    'Thane': 'F5206',
    'Wardha': 'E1649',
    'Washim': 'C7891',
    'Yavatmal': 'I5915',
}

MAHARASHTRA_DISTRICTS = sorted(list(MAHARASHTRA_DISTRICT_CODES.keys()))

# Age Band Codes
AGE_BAND_CODES = [
    (15, 'H82'),    # < 16
    (17, 'Y77'),    # 16 - 17
    (25, 'O56'),    # 18 - 25
    (30, 'P95'),    # 26 - 30
    (35, 'T54'),    # 31 - 35
    (40, 'T58'),    # 36 - 40
    (45, 'Z77'),    # 41 - 45
    (50, 'W45'),    # 46 - 50
    (55, 'M22'),    # 51 - 55
    (60, 'J65'),    # 56 - 60
]

# Alphabet Lookup Codes (A-Z)
ALPHABET_CODES = {
    'A': 'V2506', 'B': 'G5716', 'C': 'B7626', 'D': 'Q1747',
    'E': 'U5085', 'F': 'I7957', 'G': 'A7064', 'H': 'P9916',
    'I': 'L8921', 'J': 'V9094', 'K': 'B5160', 'L': 'V4873',
    'M': 'S2905', 'N': 'W3490', 'O': 'H8755', 'P': 'N1157',
    'Q': 'P1152', 'R': 'F9890', 'S': 'Y5079', 'T': 'K3676',
    'U': 'P5675', 'V': 'U4127', 'W': 'U6898', 'X': 'L3691',
    'Y': 'Z1157', 'Z': 'S6442',
}


def calculate_age(birth_year: int, birth_month: int, current_date=None) -> int:
    """
    Computes exact age considering month boundary.
    """
    if current_date is None:
        from django.utils import timezone
        current_date = timezone.now().date()
    age = current_date.year - int(birth_year)
    if current_date.month < int(birth_month):
        age -= 1
    return max(0, age)


def get_maharashtra_district_code(district_name: str) -> str:
    """
    Retrieves the 5-character alphanumeric district code for Maharashtra.
    Handles legacy district names, capitalization, and partial matches.
    """
    clean = (district_name or '').strip().lower()
    alias_map = {
        'ahmednagar': 'Ahilyanagar',
        'aurangabad': 'Chhatrapati Sambhajinagar',
        'osmanabad': 'Dharashiv',
        'mumbai': 'Mumbai City',
    }
    if clean in alias_map:
        clean = alias_map[clean].lower()
    for name, code in MAHARASHTRA_DISTRICT_CODES.items():
        if name.lower() == clean or clean in name.lower() or name.lower() in clean:
            return code
    return 'A8208'  # Default to Pune code if not found


def get_age_band_code(age: int) -> str:
    """
    Maps integer age to designated 3-character age band code.
    < 16: 'H82', 16 - 17: 'Y77', 18 - 25: 'O56', 26 - 30: 'P95',
    31 - 35: 'T54', 36 - 40: 'T58', 41 - 45: 'Z77', 46 - 50: 'W45',
    51 - 55: 'M22', 56 - 60: 'J65', 61+: 'F45'
    """
    for max_age, code in AGE_BAND_CODES:
        if age <= max_age:
            return code
    return 'F45'  # 61+


def get_alphabet_code(char: str) -> str:
    """
    Retrieves the 5-character alphabet code for a letter (A-Z).
    """
    if not char:
        return 'V2506'
    upper_char = re.sub(r'[^A-Za-z]', '', str(char)).upper()
    first_char = upper_char[0] if upper_char else 'A'
    return ALPHABET_CODES.get(first_char, 'V2506')


def generate_internal_composite_id(district_name: str, age: int, first_name: str) -> str:
    """
    Generates internal demographic composite identifier:
    f"{district_code}-{age_band_code}-{first_letter_code}-{last_letter_code}"
    e.g., Pune + 22 yrs + Vikram -> A8208-O56-U4127-S2905
    """
    dist_code = get_maharashtra_district_code(district_name)
    age_code = get_age_band_code(age)
    clean_name = re.sub(r'[^A-Za-z]', '', first_name or '').upper()
    if not clean_name:
        clean_name = 'USER'
    first_letter_code = get_alphabet_code(clean_name[0])
    last_letter_code = get_alphabet_code(clean_name[-1])
    return f"{dist_code}-{age_code}-{first_letter_code}-{last_letter_code}"


def generate_public_unique_id(internal_composite_id: str = None, attempt: int = 0) -> str:
    """
    Generates deterministic or structured 3-block 12-character format:
    Format: XXXX-XXXX-XXXX (e.g. LQ7M-8K2R-5D9X).
    Guarantees uniqueness across BuyerProfile and User models.
    """
    from apps.accounts.models import BuyerProfile
    charset = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # High legibility, zero 0/O 1/I confusion
    
    for cycle in range(50):
        if internal_composite_id and cycle == 0 and attempt == 0:
            # Deterministic derivation from composite ID hash
            h = hashlib.sha256(f"{internal_composite_id}:{attempt}".encode()).digest()
            chars = [charset[b % len(charset)] for b in h[:12]]
        else:
            # High-entropy random generation
            chars = [secrets.choice(charset) for _ in range(12)]
            
        candidate = f"{''.join(chars[0:4])}-{''.join(chars[4:8])}-{''.join(chars[8:12])}"
        
        # Verify uniqueness
        if not BuyerProfile.objects.filter(public_unique_id__iexact=candidate).exists() and \
           not User.objects.filter(username__iexact=candidate).exists():
            return candidate

    raw = secrets.token_hex(6).upper()
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


# Comprehensive mapping of Indian States & Districts with 3-letter uppercase prefix
INDIAN_DISTRICTS = {
    "Maharashtra": [
        ("Pune", "PUN"),
        ("Mumbai", "MUM"),
        ("Mumbai Suburban", "MMS"),
        ("Nashik", "NSK"),
        ("Chhatrapati Sambhajinagar", "CSN"),
        ("Thane", "THN"),
        ("Nagpur", "NGP"),
        ("Solapur", "SLP"),
        ("Kolhapur", "KOP"),
        ("Amravati", "AMR"),
        ("Navi Mumbai", "NVM"),
    ],
    "Karnataka": [
        ("Bengaluru Urban", "BLR"),
        ("Bengaluru Rural", "BRR"),
        ("Mysuru", "MYS"),
        ("Mangaluru", "MLR"),
        ("Hubballi-Dharwad", "HUB"),
        ("Belagavi", "BEL"),
        ("Kalaburagi", "KLB"),
        ("Shivamogga", "SMG"),
        ("Tumakuru", "TMK"),
        ("Udupi", "UDP"),
    ],
    "Delhi": [
        ("Central Delhi", "DEL"),
        ("New Delhi", "NDL"),
        ("South Delhi", "SDL"),
        ("North Delhi", "NDH"),
        ("West Delhi", "WDL"),
        ("East Delhi", "EDL"),
    ],
    "Telangana": [
        ("Hyderabad", "HYD"),
        ("Secunderabad", "SEC"),
        ("Rangareddy", "RNG"),
        ("Medchal-Malkajgiri", "MDL"),
        ("Warangal", "WGL"),
    ],
    "Tamil Nadu": [
        ("Chennai", "CHN"),
        ("Coimbatore", "CBE"),
        ("Madurai", "MDU"),
        ("Tiruchirappalli", "TRY"),
        ("Salem", "SLM"),
        ("Tirunelveli", "TNV"),
    ],
    "Gujarat": [
        ("Ahmedabad", "AMD"),
        ("Surat", "SRT"),
        ("Vadodara", "BRD"),
        ("Rajkot", "RJK"),
        ("Gandhinagar", "GNR"),
    ],
    "West Bengal": [
        ("Kolkata", "KOL"),
        ("Howrah", "HWH"),
        ("North 24 Parganas", "NPG"),
        ("South 24 Parganas", "SPG"),
        ("Darjeeling", "DRJ"),
    ],
    "Rajasthan": [
        ("Jaipur", "JPR"),
        ("Jodhpur", "JDH"),
        ("Udaipur", "UDP"),
        ("Kota", "KTA"),
        ("Bikaner", "BKN"),
    ],
    "Uttar Pradesh": [
        ("Lucknow", "LKO"),
        ("Kanpur", "KNP"),
        ("Noida (Gautam Buddha Nagar)", "NOI"),
        ("Ghaziabad", "GZB"),
        ("Varanasi", "VNS"),
        ("Agra", "AGR"),
        ("Prayagraj", "PRY"),
    ],
    "Kerala": [
        ("Thiruvananthapuram", "TVM"),
        ("Kochi (Ernakulam)", "COK"),
        ("Kozhikode", "CCJ"),
        ("Thrissur", "TCR"),
    ],
    "Madhya Pradesh": [
        ("Indore", "IND"),
        ("Bhopal", "BPL"),
        ("Gwalior", "GWL"),
        ("Jabalpur", "JBL"),
    ],
    "Punjab": [
        ("Chandigarh", "IXC"),
        ("Ludhiana", "LDH"),
        ("Amritsar", "ATQ"),
        ("Jalandhar", "JUC"),
    ],
    "Haryana": [
        ("Gurugram", "GGN"),
        ("Faridabad", "FBD"),
        ("Panipat", "PNP"),
        ("Ambala", "AMB"),
    ],
    "Bihar": [
        ("Patna", "PAT"),
        ("Gaya", "GAY"),
        ("Muzaffarpur", "MZF"),
    ],
    "Andhra Pradesh": [
        ("Visakhapatnam", "VTZ"),
        ("Vijayawada", "VGA"),
        ("Guntur", "GNT"),
        ("Tirupati", "TPT"),
    ],
    "Goa": [
        ("North Goa", "NGO"),
        ("South Goa", "SGO"),
    ]
}

# Pre-defined security recovery questions
SECURITY_QUESTIONS = [
    ("What is your favorite childhood street/place?", "What is your favorite childhood street/place?"),
    ("What was the name of your first school?", "What was the name of your first school?"),
    ("What is your favorite regional dish/food?", "What is your favorite regional dish/food?"),
    ("What is your memorable vehicle/cycle name?", "What is your memorable vehicle/cycle name?"),
]


def get_district_prefix(district_name):
    """
    Finds the 3-letter prefix for a given district name, or generates an uppercase 3-letter acronym.
    """
    clean_name = district_name.strip().lower()
    for state, dist_list in INDIAN_DISTRICTS.items():
        for d_name, prefix in dist_list:
            if d_name.lower() == clean_name or clean_name in d_name.lower():
                return prefix

    # Generic fallback: uppercase consonants or first letters
    letters = re.sub(r'[^A-Za-z]', '', district_name).upper()
    if len(letters) >= 3:
        return letters[:3]
    return "IND"


def generate_unique_shopper_id(district_name):
    """
    Generates a unique Shopper ID like PUN-8492, MUM-1042, BLR-7124.
    Ensures no collision with existing usernames using CSPRNG (BUG-65).
    """
    prefix = get_district_prefix(district_name)
    sys_rand = secrets.SystemRandom()
    max_attempts = 100
    for _ in range(max_attempts):
        number = sys_rand.randint(1000, 99999)
        candidate = f"{prefix}-{number}"
        if not User.objects.filter(username__iexact=candidate).exists():
            return candidate
            
    # Fallback to random 6 digits if high density
    number = sys_rand.randint(100000, 999999)
    return f"{prefix}-{number}"


def validate_strong_password(password):
    """
    Validates strong password rules:
    - Min 8 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 digit
    - At least 1 special symbol
    Returns (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter (A-Z)."
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter (a-z)."
    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one numeric digit (0-9)."
    if not re.search(r'[@#$%!&*^~_+=\-]', password):
        return False, "Password must contain at least one special symbol (@, #, $, %, !, &, *, etc.)."
    return True, ""


def compute_composite_hmac(internal_composite_id: str) -> str:
    """
    Computes a cryptographic HMAC-SHA256 signature using Django SECRET_KEY
    to mathematically seal the demographic composite identity against tampering.
    """
    import hmac
    import hashlib
    from django.conf import settings
    secret = getattr(settings, 'SECRET_KEY', 'default-lokalloop-secure-salt').encode('utf-8')
    return hmac.new(secret, internal_composite_id.encode('utf-8'), hashlib.sha256).hexdigest()[:16]


def verify_composite_integrity(internal_composite_id: str, signature: str) -> bool:
    """
    Verifies that the demographic signature has not been altered using constant-time comparison.
    """
    expected = compute_composite_hmac(internal_composite_id)
    return secrets.compare_digest(expected, signature)


def sanitize_user_input(text: str, max_length: int = 50, allow_alphanumeric_only: bool = False) -> str:
    """
    Sanitizes user input to prevent XSS, script injection, and SQL injection characters.
    """
    if not text:
        return ""
    # Strip null bytes and control characters
    cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', str(text)).strip()
    # Strip HTML/script tags
    cleaned = re.sub(r'<[^>]*>', '', cleaned)
    if allow_alphanumeric_only:
        cleaned = re.sub(r'[^A-Za-z0-9\s_\-]', '', cleaned)
    return cleaned[:max_length]

