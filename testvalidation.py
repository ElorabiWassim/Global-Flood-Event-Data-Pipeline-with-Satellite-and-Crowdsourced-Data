from validation.models.emdat import EmdatEvent


sample = EmdatEvent(
    source_event_id="1900-0006-JAM",
    event_name="Test Flood",
    main_cause="Flood",
    date_start="1900-01-06",
    date_end="1900-01-10",
    country="Jamaica",
    latitude=18.1096,
    longitude=-77.2975,
    deaths=10,
    displaced=100,
    affected=500,
    severity=0.5,
    flood_impact_index=11.5,
    h3_index="dummy_h3",
    river_basin="Test Basin",
)

print(sample)
print("Validation works")