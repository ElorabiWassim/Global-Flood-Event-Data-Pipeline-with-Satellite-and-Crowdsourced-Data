with emdat as (
    select * from {{ ref('stg_emdat') }}
),
dfo as (
    select * from {{ ref('stg_dfo') }}
)
select * from emdat
union all
select * from dfo