-- Deterministic illustrative fixture, not measured NEM or solver output.
select region, sum(mw)::integer as total_mw
from {{ ref('example_loads') }}
group by region
