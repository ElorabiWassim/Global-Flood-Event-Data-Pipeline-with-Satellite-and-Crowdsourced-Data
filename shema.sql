create extension if not exists postgis;

create table if not exists flood_events (
    id serial primary key,
    source text not null,
    source_event_id text not null,
    event_name text,
    main_cause text,
    date_start timestamp not null,
    date_end timestamp,
    country text,
    latitude double precision,
    longitude double precision,
    geometry geometry(Point, 4326),
    deaths integer default 0,
    displaced integer default 0,
    affected integer default 0,
    severity double precision,
    flood_impact_index double precision,
    glide_number text,
    url text,
    h3_index text,
    river_basin text,
    created_at timestamp default now(),
    updated_at timestamp default now(),
    constraint uq_flood_events_source unique (source, source_event_id)
);

create index if not exists idx_flood_events_source on flood_events (source);
create index if not exists idx_flood_events_date_start on flood_events (date_start);
create index if not exists idx_flood_events_country on flood_events (country);
create index if not exists idx_flood_events_h3_index on flood_events (h3_index);
create index if not exists idx_flood_events_geometry on flood_events using gist (geometry);