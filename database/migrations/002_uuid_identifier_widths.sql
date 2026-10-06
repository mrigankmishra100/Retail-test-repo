-- Align legacy identifier columns with database/schema.sql and backend UUIDs.
-- Run as the existing table owner using SQL Developer Run Script (F5) or SQLcl.
-- Stop application writers first. Oracle DDL commits implicitly and cannot be
-- rolled back. This migration only widens columns; it does not delete or rewrite rows.
-- All three columns are checked before DDL. Re-running skips adequate widths.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF
SET SERVEROUTPUT ON

DECLARE
    column_type VARCHAR2(128);
    column_width NUMBER;
    column_semantics VARCHAR2(1);
BEGIN
    FOR target IN (
        SELECT 'REPLENISHMENT_CASES' table_name, 'CASE_ID' column_name FROM dual
        UNION ALL SELECT 'APPROVED_MEMORY', 'CASE_ID' FROM dual
        UNION ALL SELECT 'APPROVED_MEMORY', 'MEMORY_ID' FROM dual
    ) LOOP
        SELECT data_type, char_length INTO column_type, column_width
        FROM user_tab_columns
        WHERE table_name = target.table_name AND column_name = target.column_name;
        IF column_type <> 'VARCHAR2' OR column_width IS NULL THEN
            RAISE_APPLICATION_ERROR(-20001, 'Unexpected identifier type. Stop and inspect the schema.');
        END IF;
    END LOOP;

    FOR target IN (
        SELECT 'REPLENISHMENT_CASES' table_name, 'CASE_ID' column_name FROM dual
        UNION ALL SELECT 'APPROVED_MEMORY', 'CASE_ID' FROM dual
        UNION ALL SELECT 'APPROVED_MEMORY', 'MEMORY_ID' FROM dual
    ) LOOP
        SELECT char_length, char_used INTO column_width, column_semantics
        FROM user_tab_columns
        WHERE table_name = target.table_name AND column_name = target.column_name;
        IF column_width < 36 THEN
            -- Names come exclusively from the fixed list above.
            EXECUTE IMMEDIATE 'ALTER TABLE ' || target.table_name ||
                ' MODIFY (' || target.column_name || ' VARCHAR2(36 ' ||
                CASE WHEN column_semantics = 'C' THEN 'CHAR' ELSE 'BYTE' END || '))';
            DBMS_OUTPUT.PUT_LINE('Widened ' || target.table_name || '.' || target.column_name || ' to 36.');
        ELSE
            DBMS_OUTPUT.PUT_LINE('Already sufficient: ' || target.table_name || '.' || target.column_name);
        END IF;
    END LOOP;
END;
/

SELECT table_name, column_name, data_type, char_length
FROM user_tab_columns
WHERE (table_name = 'REPLENISHMENT_CASES' AND column_name = 'CASE_ID')
   OR (table_name = 'APPROVED_MEMORY' AND column_name IN ('CASE_ID', 'MEMORY_ID'))
ORDER BY table_name, column_name;
