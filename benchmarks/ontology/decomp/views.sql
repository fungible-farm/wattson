-- Governed views for the decomposition candidate (#128). These are the only relations a decomp plan may read.
-- Why they exist: every convention the baseline model gets wrong on this benchmark is baked in here once, so a plan
-- says "what" and the view says "how". Rebuilt by scripts/ontology_eval/decomp_setup.py, which runs this file inside
-- localhost/wattson-nem:local against out/nem/nem.duckdb. The five parquet files are visible inside that container as
-- TEMP views named after the raw tables (dispatchload, dudetailsummary, dispatchprice, dispatchregionsum,
-- dispatchinterconnectorres) and every view below reads exactly one of them, so a query that uses only these views
-- touches no file path and needs no raw-table name.
-- Column names keep the NEMOSIS spelling so a model's prior knowledge survives; the hint comment carries the trap.

CREATE OR REPLACE VIEW interval_energy AS
SELECT
    "SETTLEMENTDATE"                                   AS "SETTLEMENTDATE",
    "DUID"                                             AS "DUID",
    CAST("TOTALCLEARED" AS DOUBLE)                     AS "TOTALCLEARED",
    CAST("TOTALCLEARED" AS DOUBLE) * 5.0 / 60.0        AS "ENERGY_MWH",
    "INITIALMW"                                        AS "INITIALMW",
    "DISPATCHMODE"                                     AS "DISPATCHMODE",
    "AGCSTATUS"                                        AS "AGCSTATUS"
FROM dispatchload
WHERE COALESCE("INTERVENTION", 0) = 0;
COMMENT ON COLUMN interval_energy."SETTLEMENTDATE" IS 'interval END timestamp, 5-minute grid; already filtered to INTERVENTION = 0, do NOT add an INTERVENTION predicate';
COMMENT ON COLUMN interval_energy."DUID" IS 'dispatch unit id; join unit_dim on this to get station/region, one row per unit so the join cannot fan out';
COMMENT ON COLUMN interval_energy."TOTALCLEARED" IS 'cleared dispatch for the interval in MW (a power, not an energy)';
COMMENT ON COLUMN interval_energy."ENERGY_MWH" IS 'PRE-COMPUTED energy = TOTALCLEARED * 5/60 MWh; use this for any energy/consumption question, use TOTALCLEARED for a power question (peak MW, average MW)';
COMMENT ON COLUMN interval_energy."INITIALMW" IS 'pre-dispatch target MW for the interval';
COMMENT ON COLUMN interval_energy."DISPATCHMODE" IS 'AGC or SEMISCHEDULE (rooftop PV is usually SEMISCHEDULE)';
COMMENT ON COLUMN interval_energy."AGCSTATUS" IS 'Y = the unit follows AGC; U = unit does not respond';

CREATE OR REPLACE VIEW unit_dim AS
SELECT
    "DUID"                    AS "DUID",
    "STATIONID"               AS "STATIONID",
    "REGIONID"                AS "REGIONID",
    "CONNECTIONPOINTID"       AS "CONNECTIONPOINTID",
    "PARTICIPANTID"           AS "PARTICIPANTID",
    "DISPATCHTYPE"            AS "DISPATCHTYPE",
    "SCHEDULE_TYPE"           AS "SCHEDULE_TYPE",
    CAST("TRANSMISSIONLOSSFACTOR" AS DOUBLE) AS "TRANSMISSIONLOSSFACTOR",
    "START_DATE"              AS "START_DATE",
    "END_DATE"                AS "END_DATE"
FROM (
    SELECT u.*, ROW_NUMBER() OVER (PARTITION BY "DUID" ORDER BY "START_DATE" DESC, "LASTCHANGED" DESC) AS rn
    FROM dudetailsummary u
) t
WHERE rn = 1;
COMMENT ON COLUMN unit_dim."DUID" IS 'dispatch unit id - exactly one row per DUID (latest registration version); join interval_energy on this, it cannot duplicate fact rows';
COMMENT ON COLUMN unit_dim."STATIONID" IS 'power station name; a station has several DUIDs (one per unit), a DUID belongs to one station and one region';
COMMENT ON COLUMN unit_dim."REGIONID" IS 'dispatch region: NSW1, QLD1, SA1, TAS1, VIC1';
COMMENT ON COLUMN unit_dim."DISPATCHTYPE" IS 'GENERATOR, LOAD or BIDIRECTIONAL; filter GENERATOR when a question says generator or unit';
COMMENT ON COLUMN unit_dim."SCHEDULE_TYPE" IS 'SCHED, SEMI or NON-SCHED dispatch';
COMMENT ON COLUMN unit_dim."START_DATE" IS 'this registration row is effective from here; the view already picked the newest row per DUID';

-- convenience view: energy already attributed to station and region (the join the baseline has to get right)
CREATE OR REPLACE VIEW unit_region AS
SELECT e."SETTLEMENTDATE" AS "SETTLEMENTDATE", e."DUID" AS "DUID", u."STATIONID" AS "STATIONID", u."REGIONID" AS "REGIONID",
       u."DISPATCHTYPE" AS "DISPATCHTYPE", e."TOTALCLEARED" AS "TOTALCLEARED", e."ENERGY_MWH" AS "ENERGY_MWH"
FROM interval_energy e
JOIN unit_dim u ON u."DUID" = e."DUID";
COMMENT ON COLUMN unit_region."STATIONID" IS 'station of the unit; one row per unit-interval with region and station attached and energy in MWh';
COMMENT ON COLUMN unit_region."DISPATCHTYPE" IS 'GENERATOR / LOAD / BIDIRECTIONAL';

CREATE OR REPLACE VIEW price_interval AS
SELECT "SETTLEMENTDATE" AS "SETTLEMENTDATE", "REGIONID" AS "REGIONID", CAST("RRP" AS DOUBLE) AS "RRP",
       "PRICE_STATUS" AS "PRICE_STATUS"
FROM dispatchprice
WHERE COALESCE("INTERVENTION", 0) = 0;
COMMENT ON COLUMN price_interval."RRP" IS 'regional reference price $/MWh for the interval, can be negative; INTERVENTION = 0 rows only';
COMMENT ON COLUMN price_interval."SETTLEMENTDATE" IS 'interval END timestamp, same 5-minute grid as interval_energy';

CREATE OR REPLACE VIEW interchange_interval AS
SELECT "SETTLEMENTDATE" AS "SETTLEMENTDATE", "INTERCONNECTORID" AS "INTERCONNECTORID",
       CAST("MWFLOW" AS DOUBLE) AS "MWFLOW", CAST("MWLOSSES" AS DOUBLE) AS "MWLOSSES",
       CAST("METEREDMWFLOW" AS DOUBLE) AS "METEREDMWFLOW"
FROM dispatchinterconnectorres
WHERE COALESCE("INTERVENTION", 0) = 0;
COMMENT ON COLUMN interchange_interval."INTERCONNECTORID" IS 'interconnector id, e.g. VIC1-NSW1, NSW1-QLD1';
COMMENT ON COLUMN interchange_interval."MWFLOW" IS 'scheduled flow in MW, POSITIVE in the forward direction of the interconnector (for VIC1-NSW1 that is VIC1 -> NSW1)';
COMMENT ON COLUMN interchange_interval."MWLOSSES" IS 'electrical losses on the link for the interval in MW';

CREATE OR REPLACE VIEW region_reserve AS
SELECT "SETTLEMENTDATE" AS "SETTLEMENTDATE", "REGIONID" AS "REGIONID",
       CAST("TOTALDEMAND" AS DOUBLE)            AS "TOTALDEMAND",
       CAST("AVAILABLEGENERATION" AS DOUBLE)    AS "AVAILABLEGENERATION",
       CAST("DEMANDFORECAST" AS DOUBLE)         AS "DEMANDFORECAST",
       CAST("NETINTERCHANGE" AS DOUBLE)         AS "NETINTERCHANGE",
       CAST("RAISE6SECLOCALDISPATCH" AS DOUBLE) AS "RAISE6SEC",
       CAST("RAISE60SECLOCALDISPATCH" AS DOUBLE) AS "RAISE60SEC",
       CAST("RAISE5MINLOCALDISPATCH" AS DOUBLE) AS "RAISE5MIN",
       CAST("RAISEREGLOCALDISPATCH" AS DOUBLE)  AS "RAISEREG"
FROM dispatchregionsum
WHERE COALESCE("INTERVENTION", 0) = 0;
COMMENT ON COLUMN region_reserve."NETINTERCHANGE" IS 'net interchange of the region in MW, positive = exporting';
COMMENT ON COLUMN region_reserve."AVAILABLEGENERATION" IS 'spare generation capacity available in the region, MW';
COMMENT ON COLUMN region_reserve."RAISE6SEC" IS 'raise-6-second reserve procured locally, MW (RAISE60SEC / RAISE5MIN / RAISEREG likewise)';

-- helper view: the interval-end day convention (SETTLEMENTDATE is the END of the interval, so day D = (D 00:00, D+1 00:00])
CREATE OR REPLACE VIEW day_interval AS
SELECT "SETTLEMENTDATE" AS "SETTLEMENTDATE", "DUID" AS "DUID", "STATIONID" AS "STATIONID", "REGIONID" AS "REGIONID",
       "DISPATCHTYPE" AS "DISPATCHTYPE", "TOTALCLEARED" AS "TOTALCLEARED", "ENERGY_MWH" AS "ENERGY_MWH",
       date_trunc('day', "SETTLEMENTDATE" - INTERVAL '5' MINUTE) AS "DAY"
FROM unit_region;
COMMENT ON COLUMN day_interval."DAY" IS 'start of the market day the interval belongs to (TIMESTAMP), using the interval-END convention: the interval ending 00:00 belongs to the PREVIOUS day, so DAY = date_trunc(settlementdate - 5 minutes). Group or filter on this instead of date_trunc(SETTLEMENTDATE)';
