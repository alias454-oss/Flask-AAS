# seed_data.py
from datetime import datetime
from app import create_app, db
from app.models.country import Country
from app.models.state import State
from app.models.env_settings import EnvSettings
from app.models.user import User
from app.models.role import Role


def seed_roles():
    roles = [
        {"name": "admin", "description": "Administrator with full access"},
        {"name": "editor", "description": "Can edit content"},
        {"name": "moderator", "description": "Can moderate user content"},
        {"name": "user", "description": "Regular user with limited permissions"},
        {"name": "guest", "description": "Guest user with minimal access"},
    ]

    for role_data in roles:
        existing = Role.query.filter_by(name=role_data["name"]).first()
        if not existing:
            role = Role(**role_data)
            db.session.add(role)

    db.session.commit()
    print("Roles seeded successfully.")


def seed_admin_user():
    # Ensure the "admin" role exists (or get it if it already does)
    admin_role = Role.query.filter_by(name='admin').first()
    if not admin_role:
        admin_role = Role(name='admin', description='Administrator with full access')
        db.session.add(admin_role)
        db.session.commit()

    # Check if the user already exists
    if User.query.filter_by(username='admin').first():
        print("Admin user already exists.")
        return

    admin_user = {
        "ip_address": "127.0.0.1",
        "username": "admin",
        "email": "admin@yoursite.com",
        "company_name": "Alias 454 Studios",
        "first_name": "admin",
        "last_name": "admin",
        "phone": "+01 000-000-0000 x12345",
        "country": 223,
        "address": "123 Somewhere",
        "city": "Some City",
        "state": "illinois",
        "zip": "61032",
        "reg_date": datetime(2025, 1, 1, 12, 00, 30),
        "last_active": datetime(2025, 7, 15, 21, 50, 30),
        "activated": True,
        "approved": True,
        "otp_secret": None,
        "mfa_enabled": False
    }

    # Require secret from config
    admin_secret = app.config.get("ADMIN_SECRET")
    if not admin_secret:
        raise ValueError("ADMIN_SECRET is not set in config")

    # Create and configure user
    user = User(**admin_user)
    user.set_password(admin_secret)
    user.roles.append(admin_role)

    # Save to DB
    db.session.add(user)
    db.session.commit()
    print("Admin user created.")


def seed_env_settings():
    # Ensure the "user" role exists (or get it if it already does)
    user_role = Role.query.filter_by(name='user').first()
    if not user_role:
        user_role = Role(name='user', description='Regular user with limited permissions')
        db.session.add(user_role)
        db.session.commit()

    settings_data = {
        "user_id": 1,
        "site_name": "Login Site",
        "site_url": "https://yoursite.com",
        "site_lang": "en",
        "site_timezone": "America/Chicago",
        "description": "Short description of site",
        "keywords": "Keyword, keyword one, separate, by commas",
        "admin_name": "admin",
        "admin_email": "admin@site.com",
        "site_mode": 1,  # 0 = public/multi-user, 1 = single-user
        "default_role_id": user_role.id,
        "users_per_page": 10,
        "users_stored_path": "/images/users",
        "max_failed_attempts": 5,
        "lockout_duration_seconds": 900,
        "template": "default",
        "use_verify_email": 0,
        "use_user_approval": 0,
        "use_user_location": 0,
        "use_captcha": 0,
        "maint_mode": 0,
        "visitor_tracking": 0,
        "use_fancy_urls": 0,
        "enable_delete_old_users": 0,
        "users_delete_after_days": 15,
        "email_after_days": 45,
        "use_smtp": 0,
        "smtp_host": "",
        "smtp_port": 25,
        "smtp_user": "",
        "smtp_pass": "",
        "enable_analytics": False,
        "allow_custom_themes": False,
        "enable_logging": True,
        "log_level": "INFO"
    }

    env = EnvSettings(**settings_data)
    db.session.add(env)
    db.session.commit()
    print("Site Base created.")


def seed_countries():
    countries = [
        {"name": "Afghanistan", "iso_code_2": "AF", "iso_code_3": "AFG"},
        {"name": "Åland Islands", "iso_code_2": "AX", "iso_code_3": "ALA"},
        {"name": "Albania", "iso_code_2": "AL", "iso_code_3": "ALB"},
        {"name": "Algeria", "iso_code_2": "DZ", "iso_code_3": "DZA"},
        {"name": "American Samoa", "iso_code_2": "AS", "iso_code_3": "ASM"},
        {"name": "Andorra", "iso_code_2": "AD", "iso_code_3": "AND"},
        {"name": "Angola", "iso_code_2": "AO", "iso_code_3": "AGO"},
        {"name": "Anguilla", "iso_code_2": "AI", "iso_code_3": "AIA"},
        {"name": "Antarctica", "iso_code_2": "AQ", "iso_code_3": "ATA"},
        {"name": "Antigua and Barbuda", "iso_code_2": "AG", "iso_code_3": "ATG"},
        {"name": "Argentina", "iso_code_2": "AR", "iso_code_3": "ARG"},
        {"name": "Armenia", "iso_code_2": "AM", "iso_code_3": "ARM"},
        {"name": "Aruba", "iso_code_2": "AW", "iso_code_3": "ABW"},
        {"name": "Australia", "iso_code_2": "AU", "iso_code_3": "AUS"},
        {"name": "Austria", "iso_code_2": "AT", "iso_code_3": "AUT"},
        {"name": "Azerbaijan", "iso_code_2": "AZ", "iso_code_3": "AZE"},
        {"name": "Bahamas", "iso_code_2": "BS", "iso_code_3": "BHS"},
        {"name": "Bahrain", "iso_code_2": "BH", "iso_code_3": "BHR"},
        {"name": "Bangladesh", "iso_code_2": "BD", "iso_code_3": "BGD"},
        {"name": "Barbados", "iso_code_2": "BB", "iso_code_3": "BRB"},
        {"name": "Belarus", "iso_code_2": "BY", "iso_code_3": "BLR"},
        {"name": "Belgium", "iso_code_2": "BE", "iso_code_3": "BEL"},
        {"name": "Belize", "iso_code_2": "BZ", "iso_code_3": "BLZ"},
        {"name": "Benin", "iso_code_2": "BJ", "iso_code_3": "BEN"},
        {"name": "Bermuda", "iso_code_2": "BM", "iso_code_3": "BMU"},
        {"name": "Bhutan", "iso_code_2": "BT", "iso_code_3": "BTN"},
        {"name": "Bolivia", "iso_code_2": "BO", "iso_code_3": "BOL"},
        {"name": "Bonaire, Sint Eustatius and Saba", "iso_code_2": "BQ", "iso_code_3": "BES"},
        {"name": "Bosnia and Herzegovina", "iso_code_2": "BA", "iso_code_3": "BIH"},
        {"name": "Botswana", "iso_code_2": "BW", "iso_code_3": "BWA"},
        {"name": "Bouvet Island", "iso_code_2": "BV", "iso_code_3": "BVT"},
        {"name": "Brazil", "iso_code_2": "BR", "iso_code_3": "BRA"},
        {"name": "British Indian Ocean Territory", "iso_code_2": "IO", "iso_code_3": "IOT"},
        {"name": "Brunei Darussalam", "iso_code_2": "BN", "iso_code_3": "BRN"},
        {"name": "Bulgaria", "iso_code_2": "BG", "iso_code_3": "BGR"},
        {"name": "Burkina Faso", "iso_code_2": "BF", "iso_code_3": "BFA"},
        {"name": "Burundi", "iso_code_2": "BI", "iso_code_3": "BDI"},
        {"name": "Cabo Verde", "iso_code_2": "CV", "iso_code_3": "CPV"},
        {"name": "Cambodia", "iso_code_2": "KH", "iso_code_3": "KHM"},
        {"name": "Cameroon", "iso_code_2": "CM", "iso_code_3": "CMR"},
        {"name": "Canada", "iso_code_2": "CA", "iso_code_3": "CAN"},
        {"name": "Cayman Islands", "iso_code_2": "KY", "iso_code_3": "CYM"},
        {"name": "Central African Republic", "iso_code_2": "CF", "iso_code_3": "CAF"},
        {"name": "Chad", "iso_code_2": "TD", "iso_code_3": "TCD"},
        {"name": "Chile", "iso_code_2": "CL", "iso_code_3": "CHL"},
        {"name": "China", "iso_code_2": "CN", "iso_code_3": "CHN"},
        {"name": "Christmas Island", "iso_code_2": "CX", "iso_code_3": "CXR"},
        {"name": "Cocos (Keeling) Islands", "iso_code_2": "CC", "iso_code_3": "CCK"},
        {"name": "Colombia", "iso_code_2": "CO", "iso_code_3": "COL"},
        {"name": "Comoros", "iso_code_2": "KM", "iso_code_3": "COM"},
        {"name": "Congo", "iso_code_2": "CG", "iso_code_3": "COG"},
        {"name": "Congo, Democratic Republic of the", "iso_code_2": "CD", "iso_code_3": "COD"},
        {"name": "Cook Islands", "iso_code_2": "CK", "iso_code_3": "COK"},
        {"name": "Costa Rica", "iso_code_2": "CR", "iso_code_3": "CRI"},
        {"name": "Croatia", "iso_code_2": "HR", "iso_code_3": "HRV"},
        {"name": "Cuba", "iso_code_2": "CU", "iso_code_3": "CUB"},
        {"name": "Curaçao", "iso_code_2": "CW", "iso_code_3": "CUW"},
        {"name": "Cyprus", "iso_code_2": "CY", "iso_code_3": "CYP"},
        {"name": "Czechia", "iso_code_2": "CZ", "iso_code_3": "CZE"},
        {"name": "Denmark", "iso_code_2": "DK", "iso_code_3": "DNK"},
        {"name": "Djibouti", "iso_code_2": "DJ", "iso_code_3": "DJI"},
        {"name": "Dominica", "iso_code_2": "DM", "iso_code_3": "DMA"},
        {"name": "Dominican Republic", "iso_code_2": "DO", "iso_code_3": "DOM"},
        {"name": "Ecuador", "iso_code_2": "EC", "iso_code_3": "ECU"},
        {"name": "Egypt", "iso_code_2": "EG", "iso_code_3": "EGY"},
        {"name": "El Salvador", "iso_code_2": "SV", "iso_code_3": "SLV"},
        {"name": "Equatorial Guinea", "iso_code_2": "GQ", "iso_code_3": "GNQ"},
        {"name": "Eritrea", "iso_code_2": "ER", "iso_code_3": "ERI"},
        {"name": "Estonia", "iso_code_2": "EE", "iso_code_3": "EST"},
        {"name": "Eswatini", "iso_code_2": "SZ", "iso_code_3": "SWZ"},
        {"name": "Ethiopia", "iso_code_2": "ET", "iso_code_3": "ETH"},
        {"name": "Falkland Islands (Malvinas)", "iso_code_2": "FK", "iso_code_3": "FLK"},
        {"name": "Faroe Islands", "iso_code_2": "FO", "iso_code_3": "FRO"},
        {"name": "Fiji", "iso_code_2": "FJ", "iso_code_3": "FJI"},
        {"name": "Finland", "iso_code_2": "FI", "iso_code_3": "FIN"},
        {"name": "France", "iso_code_2": "FR", "iso_code_3": "FRA"},
        {"name": "French Guiana", "iso_code_2": "GF", "iso_code_3": "GUF"},
        {"name": "French Polynesia", "iso_code_2": "PF", "iso_code_3": "PYF"},
        {"name": "French Southern Territories", "iso_code_2": "TF", "iso_code_3": "ATF"},
        {"name": "Gabon", "iso_code_2": "GA", "iso_code_3": "GAB"},
        {"name": "Gambia", "iso_code_2": "GM", "iso_code_3": "GMB"},
        {"name": "Georgia", "iso_code_2": "GE", "iso_code_3": "GEO"},
        {"name": "Germany", "iso_code_2": "DE", "iso_code_3": "DEU"},
        {"name": "Ghana", "iso_code_2": "GH", "iso_code_3": "GHA"},
        {"name": "Gibraltar", "iso_code_2": "GI", "iso_code_3": "GIB"},
        {"name": "Greece", "iso_code_2": "GR", "iso_code_3": "GRC"},
        {"name": "Greenland", "iso_code_2": "GL", "iso_code_3": "GRL"},
        {"name": "Grenada", "iso_code_2": "GD", "iso_code_3": "GRD"},
        {"name": "Guadeloupe", "iso_code_2": "GP", "iso_code_3": "GLP"},
        {"name": "Guam", "iso_code_2": "GU", "iso_code_3": "GUM"},
        {"name": "Guatemala", "iso_code_2": "GT", "iso_code_3": "GTM"},
        {"name": "Guinea", "iso_code_2": "GN", "iso_code_3": "GIN"},
        {"name": "Guinea-Bissau", "iso_code_2": "GW", "iso_code_3": "GNB"},
        {"name": "Guyana", "iso_code_2": "GY", "iso_code_3": "GUY"},
        {"name": "Haiti", "iso_code_2": "HT", "iso_code_3": "HTI"},
        {"name": "Heard Island and McDonald Islands", "iso_code_2": "HM", "iso_code_3": "HMD"},
        {"name": "Holy See (Vatican City State)", "iso_code_2": "VA", "iso_code_3": "VAT"},
        {"name": "Honduras", "iso_code_2": "HN", "iso_code_3": "HND"},
        {"name": "Hong Kong", "iso_code_2": "HK", "iso_code_3": "HKG"},
        {"name": "Hungary", "iso_code_2": "HU", "iso_code_3": "HUN"},
        {"name": "Iceland", "iso_code_2": "IS", "iso_code_3": "ISL"},
        {"name": "India", "iso_code_2": "IN", "iso_code_3": "IND"},
        {"name": "Indonesia", "iso_code_2": "ID", "iso_code_3": "IDN"},
        {"name": "Iran (Islamic Republic of)", "iso_code_2": "IR", "iso_code_3": "IRN"},
        {"name": "Iraq", "iso_code_2": "IQ", "iso_code_3": "IRQ"},
        {"name": "Ireland", "iso_code_2": "IE", "iso_code_3": "IRL"},
        {"name": "Israel", "iso_code_2": "IL", "iso_code_3": "ISR"},
        {"name": "Italy", "iso_code_2": "IT", "iso_code_3": "ITA"},
        {"name": "Jamaica", "iso_code_2": "JM", "iso_code_3": "JAM"},
        {"name": "Japan", "iso_code_2": "JP", "iso_code_3": "JPN"},
        {"name": "Jordan", "iso_code_2": "JO", "iso_code_3": "JOR"},
        {"name": "Kazakhstan", "iso_code_2": "KZ", "iso_code_3": "KAZ"},
        {"name": "Kenya", "iso_code_2": "KE", "iso_code_3": "KEN"},
        {"name": "Kiribati", "iso_code_2": "KI", "iso_code_3": "KIR"},
        {"name": "Korea (Democratic People's Republic of)", "iso_code_2": "KP", "iso_code_3": "PRK"},
        {"name": "Korea (Republic of)", "iso_code_2": "KR", "iso_code_3": "KOR"},
        {"name": "Kosovo", "iso_code_2": "XK", "iso_code_3": "XKX"},
        {"name": "Kuwait", "iso_code_2": "KW", "iso_code_3": "KWT"},
        {"name": "Kyrgyzstan", "iso_code_2": "KG", "iso_code_3": "KGZ"},
        {"name": "Lao People's Democratic Republic", "iso_code_2": "LA", "iso_code_3": "LAO"},
        {"name": "Latvia", "iso_code_2": "LV", "iso_code_3": "LVA"},
        {"name": "Lebanon", "iso_code_2": "LB", "iso_code_3": "LBN"},
        {"name": "Lesotho", "iso_code_2": "LS", "iso_code_3": "LSO"},
        {"name": "Liberia", "iso_code_2": "LR", "iso_code_3": "LBR"},
        {"name": "Libya", "iso_code_2": "LY", "iso_code_3": "LBY"},
        {"name": "Liechtenstein", "iso_code_2": "LI", "iso_code_3": "LIE"},
        {"name": "Lithuania", "iso_code_2": "LT", "iso_code_3": "LTU"},
        {"name": "Luxembourg", "iso_code_2": "LU", "iso_code_3": "LUX"},
        {"name": "Macao", "iso_code_2": "MO", "iso_code_3": "MAC"},
        {"name": "Madagascar", "iso_code_2": "MG", "iso_code_3": "MDG"},
        {"name": "Malawi", "iso_code_2": "MW", "iso_code_3": "MWI"},
        {"name": "Malaysia", "iso_code_2": "MY", "iso_code_3": "MYS"},
        {"name": "Maldives", "iso_code_2": "MV", "iso_code_3": "MDV"},
        {"name": "Mali", "iso_code_2": "ML", "iso_code_3": "MLI"},
        {"name": "Malta", "iso_code_2": "MT", "iso_code_3": "MLT"},
        {"name": "Marshall Islands", "iso_code_2": "MH", "iso_code_3": "MHL"},
        {"name": "Martinique", "iso_code_2": "MQ", "iso_code_3": "MTQ"},
        {"name": "Mauritania", "iso_code_2": "MR", "iso_code_3": "MRT"},
        {"name": "Mauritius", "iso_code_2": "MU", "iso_code_3": "MUS"},
        {"name": "Mayotte", "iso_code_2": "YT", "iso_code_3": "MYT"},
        {"name": "Mexico", "iso_code_2": "MX", "iso_code_3": "MEX"},
        {"name": "Micronesia, Federated States of", "iso_code_2": "FM", "iso_code_3": "FSM"},
        {"name": "Moldova, Republic of", "iso_code_2": "MD", "iso_code_3": "MDA"},
        {"name": "Monaco", "iso_code_2": "MC", "iso_code_3": "MCO"},
        {"name": "Mongolia", "iso_code_2": "MN", "iso_code_3": "MNG"},
        {"name": "Montenegro", "iso_code_2": "ME", "iso_code_3": "MNE"},
        {"name": "Montserrat", "iso_code_2": "MS", "iso_code_3": "MSR"},
        {"name": "Morocco", "iso_code_2": "MA", "iso_code_3": "MAR"},
        {"name": "Mozambique", "iso_code_2": "MZ", "iso_code_3": "MOZ"},
        {"name": "Myanmar", "iso_code_2": "MM", "iso_code_3": "MMR"},
        {"name": "Namibia", "iso_code_2": "NA", "iso_code_3": "NAM"},
        {"name": "Nauru", "iso_code_2": "NR", "iso_code_3": "NRU"},
        {"name": "Nepal", "iso_code_2": "NP", "iso_code_3": "NPL"},
        {"name": "Netherlands", "iso_code_2": "NL", "iso_code_3": "NLD"},
        {"name": "New Caledonia", "iso_code_2": "NC", "iso_code_3": "NCL"},
        {"name": "New Zealand", "iso_code_2": "NZ", "iso_code_3": "NZL"},
        {"name": "Nicaragua", "iso_code_2": "NI", "iso_code_3": "NIC"},
        {"name": "Niger", "iso_code_2": "NE", "iso_code_3": "NER"},
        {"name": "Nigeria", "iso_code_2": "NG", "iso_code_3": "NGA"},
        {"name": "Niue", "iso_code_2": "NU", "iso_code_3": "NIU"},
        {"name": "Norfolk Island", "iso_code_2": "NF", "iso_code_3": "NFK"},
        {"name": "Northern Mariana Islands", "iso_code_2": "MP", "iso_code_3": "MNP"},
        {"name": "North Macedonia", "iso_code_2": "MK", "iso_code_3": "MKD"},
        {"name": "Norway", "iso_code_2": "NO", "iso_code_3": "NOR"},
        {"name": "Oman", "iso_code_2": "OM", "iso_code_3": "OMN"},
        {"name": "Pakistan", "iso_code_2": "PK", "iso_code_3": "PAK"},
        {"name": "Palau", "iso_code_2": "PW", "iso_code_3": "PLW"},
        {"name": "Panama", "iso_code_2": "PA", "iso_code_3": "PAN"},
        {"name": "Papua New Guinea", "iso_code_2": "PG", "iso_code_3": "PNG"},
        {"name": "Paraguay", "iso_code_2": "PY", "iso_code_3": "PRY"},
        {"name": "Peru", "iso_code_2": "PE", "iso_code_3": "PER"},
        {"name": "Philippines", "iso_code_2": "PH", "iso_code_3": "PHL"},
        {"name": "Pitcairn", "iso_code_2": "PN", "iso_code_3": "PCN"},
        {"name": "Poland", "iso_code_2": "PL", "iso_code_3": "POL"},
        {"name": "Portugal", "iso_code_2": "PT", "iso_code_3": "PRT"},
        {"name": "Puerto Rico", "iso_code_2": "PR", "iso_code_3": "PRI"},
        {"name": "Qatar", "iso_code_2": "QA", "iso_code_3": "QAT"},
        {"name": "Reunion", "iso_code_2": "RE", "iso_code_3": "REU"},
        {"name": "Romania", "iso_code_2": "RO", "iso_code_3": "ROU"},
        {"name": "Russian Federation", "iso_code_2": "RU", "iso_code_3": "RUS"},
        {"name": "Rwanda", "iso_code_2": "RW", "iso_code_3": "RWA"},
        {"name": "Saint Kitts and Nevis", "iso_code_2": "KN", "iso_code_3": "KNA"},
        {"name": "Saint Lucia", "iso_code_2": "LC", "iso_code_3": "LCA"},
        {"name": "Saint Vincent and the Grenadines", "iso_code_2": "VC", "iso_code_3": "VCT"},
        {"name": "Samoa", "iso_code_2": "WS", "iso_code_3": "WSM"},
        {"name": "San Marino", "iso_code_2": "SM", "iso_code_3": "SMR"},
        {"name": "Sao Tome and Principe", "iso_code_2": "ST", "iso_code_3": "STP"},
        {"name": "Saudi Arabia", "iso_code_2": "SA", "iso_code_3": "SAU"},
        {"name": "Senegal", "iso_code_2": "SN", "iso_code_3": "SEN"},
        {"name": "Serbia", "iso_code_2": "RS", "iso_code_3": "SRB"},
        {"name": "Seychelles", "iso_code_2": "SC", "iso_code_3": "SYC"},
        {"name": "Sierra Leone", "iso_code_2": "SL", "iso_code_3": "SLE"},
        {"name": "Singapore", "iso_code_2": "SG", "iso_code_3": "SGP"},
        {"name": "Slovakia (Slovak Republic)", "iso_code_2": "SK", "iso_code_3": "SVK"},
        {"name": "Slovenia", "iso_code_2": "SI", "iso_code_3": "SVN"},
        {"name": "Solomon Islands", "iso_code_2": "SB", "iso_code_3": "SLB"},
        {"name": "Somalia", "iso_code_2": "SO", "iso_code_3": "SOM"},
        {"name": "South Africa", "iso_code_2": "ZA", "iso_code_3": "ZAF"},
        {"name": "South Georgia and the South Sandwich Islands", "iso_code_2": "GS", "iso_code_3": "SGS"},
        {"name": "Spain", "iso_code_2": "ES", "iso_code_3": "ESP"},
        {"name": "Sri Lanka", "iso_code_2": "LK", "iso_code_3": "LKA"},
        {"name": "St. Helena", "iso_code_2": "SH", "iso_code_3": "SHN"},
        {"name": "St. Pierre and Miquelon", "iso_code_2": "PM", "iso_code_3": "SPM"},
        {"name": "Sudan", "iso_code_2": "SD", "iso_code_3": "SDN"},
        {"name": "South Sudan", "iso_code_2": "SS", "iso_code_3": "SSD"},
        {"name": "Suriname", "iso_code_2": "SR", "iso_code_3": "SUR"},
        {"name": "Svalbard and Jan Mayen Islands", "iso_code_2": "SJ", "iso_code_3": "SJM"},
        {"name": "Sweden", "iso_code_2": "SE", "iso_code_3": "SWE"},
        {"name": "Switzerland", "iso_code_2": "CH", "iso_code_3": "CHE"},
        {"name": "Syria", "iso_code_2": "SY", "iso_code_3": "SYR"},
        {"name": "Taiwan", "iso_code_2": "TW", "iso_code_3": "TWN"},
        {"name": "Tajikistan", "iso_code_2": "TJ", "iso_code_3": "TJK"},
        {"name": "Tanzania, United Republic of", "iso_code_2": "TZ", "iso_code_3": "TZA"},
        {"name": "Thailand", "iso_code_2": "TH", "iso_code_3": "THA"},
        {"name": "Timor-Leste", "iso_code_2": "TL", "iso_code_3": "TLS"},
        {"name": "Togo", "iso_code_2": "TG", "iso_code_3": "TGO"},
        {"name": "Tokelau", "iso_code_2": "TK", "iso_code_3": "TKL"},
        {"name": "Tonga", "iso_code_2": "TO", "iso_code_3": "TON"},
        {"name": "Trinidad and Tobago", "iso_code_2": "TT", "iso_code_3": "TTO"},
        {"name": "Tunisia", "iso_code_2": "TN", "iso_code_3": "TUN"},
        {"name": "Turkey", "iso_code_2": "TR", "iso_code_3": "TUR"},
        {"name": "Turkmenistan", "iso_code_2": "TM", "iso_code_3": "TKM"},
        {"name": "Turks and Caicos Islands", "iso_code_2": "TC", "iso_code_3": "TCA"},
        {"name": "Tuvalu", "iso_code_2": "TV", "iso_code_3": "TUV"},
        {"name": "Uganda", "iso_code_2": "UG", "iso_code_3": "UGA"},
        {"name": "Ukraine", "iso_code_2": "UA", "iso_code_3": "UKR"},
        {"name": "United Arab Emirates", "iso_code_2": "AE", "iso_code_3": "ARE"},
        {"name": "United Kingdom", "iso_code_2": "GB", "iso_code_3": "GBR"},
        {"name": "United States", "iso_code_2": "US", "iso_code_3": "USA"},
        {"name": "United States Minor Outlying Islands", "iso_code_2": "UM", "iso_code_3": "UMI"},
        {"name": "Uruguay", "iso_code_2": "UY", "iso_code_3": "URY"},
        {"name": "Uzbekistan", "iso_code_2": "UZ", "iso_code_3": "UZB"},
        {"name": "Vanuatu", "iso_code_2": "VU", "iso_code_3": "VUT"},
        {"name": "Venezuela", "iso_code_2": "VE", "iso_code_3": "VEN"},
        {"name": "Vietnam", "iso_code_2": "VN", "iso_code_3": "VNM"},
        {"name": "Virgin Islands (British)", "iso_code_2": "VG", "iso_code_3": "VGB"},
        {"name": "Virgin Islands (U.S.)", "iso_code_2": "VI", "iso_code_3": "VIR"},
        {"name": "Wallis and Futuna Islands", "iso_code_2": "WF", "iso_code_3": "WLF"},
        {"name": "Western Sahara", "iso_code_2": "EH", "iso_code_3": "ESH"},
        {"name": "Yemen", "iso_code_2": "YE", "iso_code_3": "YEM"},
        {"name": "Zambia", "iso_code_2": "ZM", "iso_code_3": "ZMB"},
        {"name": "Zimbabwe", "iso_code_2": "ZW", "iso_code_3": "ZWE"}
    ]

    for data in countries:
        country = Country(
            name=data["name"],
            iso_code_2=data["iso_code_2"],
            iso_code_3=data["iso_code_3"],
            address_format=""  # customize as needed
        )
        db.session.add(country)

    db.session.commit()
    print(f"Seeded {len(countries)} countries.")


def seed_states():
    states = [
        {"prefix": "AL", "name": "Alabama"},
        {"prefix": "AK", "name": "Alaska"},
        {"prefix": "AS", "name": "American Samoa"},
        {"prefix": "AZ", "name": "Arizona"},
        {"prefix": "AR", "name": "Arkansas"},
        {"prefix": "CA", "name": "California"},
        {"prefix": "CO", "name": "Colorado"},
        {"prefix": "CT", "name": "Connecticut"},
        {"prefix": "DE", "name": "Delaware"},
        {"prefix": "DC", "name": "District of Columbia"},
        {"prefix": "FM", "name": "Federated States of Micronesia"},
        {"prefix": "FL", "name": "Florida"},
        {"prefix": "GA", "name": "Georgia"},
        {"prefix": "GU", "name": "Guam"},
        {"prefix": "HI", "name": "Hawaii"},
        {"prefix": "ID", "name": "Idaho"},
        {"prefix": "IL", "name": "Illinois"},
        {"prefix": "IN", "name": "Indiana"},
        {"prefix": "IA", "name": "Iowa"},
        {"prefix": "KS", "name": "Kansas"},
        {"prefix": "KY", "name": "Kentucky"},
        {"prefix": "LA", "name": "Louisiana"},
        {"prefix": "ME", "name": "Maine"},
        {"prefix": "MH", "name": "Marshall Islands"},
        {"prefix": "MD", "name": "Maryland"},
        {"prefix": "MA", "name": "Massachusetts"},
        {"prefix": "MI", "name": "Michigan"},
        {"prefix": "MN", "name": "Minnesota"},
        {"prefix": "MS", "name": "Mississippi"},
        {"prefix": "MO", "name": "Missouri"},
        {"prefix": "MT", "name": "Montana"},
        {"prefix": "NE", "name": "Nebraska"},
        {"prefix": "NV", "name": "Nevada"},
        {"prefix": "NH", "name": "New Hampshire"},
        {"prefix": "NJ", "name": "New Jersey"},
        {"prefix": "NM", "name": "New Mexico"},
        {"prefix": "NY", "name": "New York"},
        {"prefix": "NC", "name": "North Carolina"},
        {"prefix": "ND", "name": "North Dakota"},
        {"prefix": "MP", "name": "Northern Mariana Islands"},
        {"prefix": "OH", "name": "Ohio"},
        {"prefix": "OK", "name": "Oklahoma"},
        {"prefix": "OR", "name": "Oregon"},
        {"prefix": "PW", "name": "Palau"},
        {"prefix": "PA", "name": "Pennsylvania"},
        {"prefix": "PR", "name": "Puerto Rico"},
        {"prefix": "RI", "name": "Rhode Island"},
        {"prefix": "SC", "name": "South Carolina"},
        {"prefix": "SD", "name": "South Dakota"},
        {"prefix": "TN", "name": "Tennessee"},
        {"prefix": "TX", "name": "Texas"},
        {"prefix": "UT", "name": "Utah"},
        {"prefix": "VT", "name": "Vermont"},
        {"prefix": "VI", "name": "Virgin Islands"},
        {"prefix": "VA", "name": "Virginia"},
        {"prefix": "WA", "name": "Washington"},
        {"prefix": "WV", "name": "West Virginia"},
        {"prefix": "WI", "name": "Wisconsin"},
        {"prefix": "WY", "name": "Wyoming"}
    ]

    for data in states:
        state = State(
            state_prefix=data["prefix"],
            state_name=data["name"]
        )
        db.session.add(state)

    db.session.commit()
    print("Seeded states.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_roles()  # Seed roles first
        seed_admin_user()  # Create admin user and assign roles
        seed_env_settings()  # Seed environment settings
        seed_countries()  # Seed countries, states, other reference data
        seed_states()
