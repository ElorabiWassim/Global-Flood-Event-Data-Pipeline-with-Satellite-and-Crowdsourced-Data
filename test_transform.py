import pandas as pd
from transforms.emdat import transform_emdat_row

row = {
    "DisNo.": "1900-0006-JAM",
    "Event Name": "Historic Flood",
    "Disaster Type": "Flood",
    "Disaster Subtype": "Riverine flood",
    "Country": "Jamaica",
    "Latitude": 18.1096,
    "Longitude": -77.2975,
    "River Basin": "Black River",
    "Start Year": 1900,
    "Start Month": 1,
    "Start Day": 6,
    "End Year": 1900,
    "End Month": 1,
    "End Day": 10,
    "Total Deaths": 10,
    "No. Homeless": 100,
    "Total Affected": 500,
    "Total Damage ('000 US$)": 250,
}

row = pd.Series(row)

result = transform_emdat_row(row, 7)

print(result)
print("Transformation works")