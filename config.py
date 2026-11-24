"""Configuration for Hartford County Price Transparency Pipeline."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "hartford_prices.db"
RAW_HOSPITAL_DIR = BASE_DIR / "data" / "hospital_files"
RAW_INSURER_DIR = BASE_DIR / "data" / "insurer_files"

# Google Drive Folder: "Project Price Transparency" -> "Hospital Files"
DRIVE_HOSPITAL_FOLDER_ID = "1iIOSHj8mmBW_Jd_br1Jh-b0PMq2cdGtC"

DRIVE_HOSPITAL_FILES = [
    {
        "file_id": "1eGZ5c1RY3RssSICaFjcHgqQbYimShTLP",
        "filename": "060646768_the_hospital_of_central_connecticut_standardcharges.csv",
        "hospital_name": "The Hospital of Central Connecticut",
        "ein": "06-0646768",
    },
    {
        "file_id": "13UegQPnblTUlHSK1HQ9D8rAaDwbbowfZ",
        "filename": "060646668_hartford_hospital_standardcharges.csv",
        "hospital_name": "Hartford Hospital",
        "ein": "06-0646668",
    },
    {
        "file_id": "1K85pGOl2u_CGDklKaL_WYMZaZRXkThfV",
        "filename": "393859332_manchester-memorial-hospital_standardcharges.csv",
        "hospital_name": "Manchester Memorial Hospital",
        "ein": "39-3859332",
    },
    {
        "file_id": "1yDz6P3g5-OMtMe5kClJQSFax2RQdS84A",
        "filename": "060646813_saint-francis-hospital-and-medical-center_standardcharges.zip",
        "hospital_name": "Saint Francis Hospital and Medical Center",
        "ein": "06-0646813",
    },
    {
        "file_id": "1PBPIw6oSG-UVRT0a5MENxd-2KVNc2kNf",
        "filename": "475676956_Johnson-Memorial-Hospital_standardcharges.csv",
        "hospital_name": "Johnson Memorial Hospital",
        "ein": "47-5676956",
    },
]

# 30 Target CPT Codes requested for Hartford County Price Comparison
TARGET_CPTS = {
    "27447": ("Total knee arthroplasty", "Orthopedics"),
    "27130": ("Total hip arthroplasty", "Orthopedics"),
    "29881": ("Knee arthroscopy with meniscectomy (medial or lateral)", "Orthopedics"),
    "29827": ("Shoulder arthroscopy with rotator cuff repair", "Orthopedics"),
    "29888": ("Knee arthroscopy with ACL reconstruction", "Orthopedics"),
    "64721": ("Carpal tunnel release (median nerve neuroplasty)", "Orthopedics / Nerve"),
    "66984": ("Cataract surgery with intraocular lens (1-stage)", "Ophthalmology"),
    "49505": ("Repair initial inguinal hernia (age 5+)", "General Surgery"),
    "47562": ("Laparoscopic cholecystectomy (gallbladder removal)", "General Surgery"),
    "45378": ("Diagnostic colonoscopy, flexible", "GI / Endoscopy"),
    "45380": ("Colonoscopy with biopsy", "GI / Endoscopy"),
    "45385": ("Colonoscopy with polyp/lesion removal", "GI / Endoscopy"),
    "43239": ("Upper GI endoscopy (EGD) with biopsy", "GI / Endoscopy"),
    "70551": ("MRI brain without contrast", "Imaging"),
    "72148": ("MRI lumbar spine without contrast", "Imaging"),
    "73721": ("MRI lower extremity joint without contrast", "Imaging"),
    "73221": ("MRI upper extremity joint without contrast", "Imaging"),
    "74177": ("CT abdomen and pelvis with contrast", "Imaging"),
    "71250": ("CT chest without contrast", "Imaging"),
    "70450": ("CT head/brain without contrast", "Imaging"),
    "76700": ("Abdominal ultrasound, complete", "Imaging"),
    "77080": ("DEXA bone density scan (axial skeleton)", "Imaging"),
    "93306": ("Transthoracic echocardiogram (TTE) complete with Doppler", "Cardiology"),
    "93015": ("Cardiovascular stress test", "Cardiology"),
    "95810": ("Polysomnography (sleep study), 4+ parameters", "Sleep Medicine"),
    "97110": ("Therapeutic exercises (15-min unit)", "Physical Therapy"),
    "80053": ("Comprehensive metabolic panel (CMP)", "Lab"),
    "80061": ("Lipid panel", "Lab"),
    "85025": ("Complete blood count (CBC) with automated differential", "Lab"),
    "83036": ("Hemoglobin A1c (glycated hemoglobin)", "Lab"),
}

# Public Transparency in Coverage (TiC) monthly MRF portals for major CT/Hartford insurers
INSURER_TIC_PORTALS = {
    "Aetna": "https://health1.aetna.com/app/public/#/one/insurerCode=AETNACVS_I&brandCode=ALICFI/machine-readable-transparency-in-coverage",
    "Anthem BCBS": "https://www.anthem.com/machine-readable-file/search/",
    "UnitedHealthcare": "https://transparency-in-coverage.uhc.com/",
    "Cigna": "https://www.cigna.com/legal/compliance/machine-readable-files",
    "ConnectiCare": "https://www.connecticare.com/transparency-in-coverage",
}

# Payer name normalization rules (maps messy hospital MRF payer_name strings to canonical insurers)
PAYER_NORMALIZATION_RULES = [
    ("aetna", "Aetna"),
    ("anthem", "Anthem BCBS"),
    ("blue cross", "Anthem BCBS"),
    ("bcbs", "Anthem BCBS"),
    ("cigna", "Cigna"),
    ("united", "UnitedHealthcare"),
    ("uhc", "UnitedHealthcare"),
    ("oxford", "UnitedHealthcare (Oxford)"),
    ("connecticare", "ConnectiCare"),
    ("harvard pilgrim", "Harvard Pilgrim"),
    ("medicare", "Medicare"),
    ("medicaid", "Medicaid / Husky"),
    ("husky", "Medicaid / Husky"),
    ("tricare", "TRICARE"),
]
