-- Small, deterministic dataset for the replenishment POC.

INSERT INTO items VALUES ('ITEM001', 'Ice Cream', 'Frozen', 80, 40);
INSERT INTO items VALUES ('ITEM002', 'Milk', 'Dairy', 120, 60);
INSERT INTO items VALUES ('ITEM003', 'Bread', 'Bakery', 100, 50);
INSERT INTO items VALUES ('ITEM004', 'Soft Drinks', 'Beverages', 180, 80);
INSERT INTO items VALUES ('ITEM005', 'Detergent', 'Household', 100, 40);

INSERT INTO inventory (item_id, current_quantity) VALUES ('ITEM001', 100);
INSERT INTO inventory (item_id, current_quantity) VALUES ('ITEM002', 180);
INSERT INTO inventory (item_id, current_quantity) VALUES ('ITEM003', 150);
INSERT INTO inventory (item_id, current_quantity) VALUES ('ITEM004', 300);
INSERT INTO inventory (item_id, current_quantity) VALUES ('ITEM005', 200);

INSERT INTO suppliers VALUES ('SUP001', 'Fresh Foods', 'supplier001@example.com', 'SUP001_policy.pdf');
INSERT INTO suppliers VALUES ('SUP002', 'NorthStar Distribution', 'supplier002@example.com', 'SUP002_policy.pdf');
INSERT INTO suppliers VALUES ('SUP003', 'Metro Wholesale', 'supplier003@example.com', 'SUP003_policy.pdf');
INSERT INTO suppliers VALUES ('SUP004', 'Prime Retail Supply', 'supplier004@example.com', 'SUP004_policy.pdf');

-- Exactly two or three eligible suppliers per item.
-- Price (INR per policy unit), standard lead days and MOQ match the unchanged PDFs.
INSERT INTO supplier_items VALUES ('SUP001', 'ITEM001', 900.00, 3, 100);
INSERT INTO supplier_items VALUES ('SUP002', 'ITEM001', 850.00, 5, 200);
INSERT INTO supplier_items VALUES ('SUP004', 'ITEM001', 875.00, 4, 150);
INSERT INTO supplier_items VALUES ('SUP001', 'ITEM002', 450.00, 2, 100);
INSERT INTO supplier_items VALUES ('SUP002', 'ITEM002', 425.00, 3, 150);
INSERT INTO supplier_items VALUES ('SUP001', 'ITEM003', 300.00, 2, 100);
INSERT INTO supplier_items VALUES ('SUP003', 'ITEM003', 275.00, 3, 150);
INSERT INTO supplier_items VALUES ('SUP002', 'ITEM004', 700.00, 4, 100);
INSERT INTO supplier_items VALUES ('SUP003', 'ITEM004', 680.00, 5, 200);
INSERT INTO supplier_items VALUES ('SUP004', 'ITEM004', 690.00, 3, 150);
INSERT INTO supplier_items VALUES ('SUP003', 'ITEM005', 550.00, 5, 100);
INSERT INTO supplier_items VALUES ('SUP004', 'ITEM005', 525.00, 4, 150);

INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP001', 'ITEM001', 800);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP002', 'ITEM001', 650);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP004', 'ITEM001', 400);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP001', 'ITEM002', 1200);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP002', 'ITEM002', 900);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP001', 'ITEM003', 750);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP003', 'ITEM003', 1000);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP002', 'ITEM004', 1600);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP003', 'ITEM004', 1800);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP004', 'ITEM004', 1100);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP003', 'ITEM005', 600);
INSERT INTO supplier_inventory (supplier_id, item_id, available_quantity) VALUES ('SUP004', 'ITEM005', 550);

-- Fourteen days of intentionally strong Ice Cream demand make its risk obvious.
INSERT INTO sales_history VALUES (1, 'ITEM001', TRUNC(SYSDATE) - 13, 16);
INSERT INTO sales_history VALUES (2, 'ITEM001', TRUNC(SYSDATE) - 12, 18);
INSERT INTO sales_history VALUES (3, 'ITEM001', TRUNC(SYSDATE) - 11, 15);
INSERT INTO sales_history VALUES (4, 'ITEM001', TRUNC(SYSDATE) - 10, 17);
INSERT INTO sales_history VALUES (5, 'ITEM001', TRUNC(SYSDATE) - 9, 19);
INSERT INTO sales_history VALUES (6, 'ITEM001', TRUNC(SYSDATE) - 8, 16);
INSERT INTO sales_history VALUES (7, 'ITEM001', TRUNC(SYSDATE) - 7, 20);
INSERT INTO sales_history VALUES (8, 'ITEM001', TRUNC(SYSDATE) - 6, 18);
INSERT INTO sales_history VALUES (9, 'ITEM001', TRUNC(SYSDATE) - 5, 17);
INSERT INTO sales_history VALUES (10, 'ITEM001', TRUNC(SYSDATE) - 4, 21);
INSERT INTO sales_history VALUES (11, 'ITEM001', TRUNC(SYSDATE) - 3, 18);
INSERT INTO sales_history VALUES (12, 'ITEM001', TRUNC(SYSDATE) - 2, 20);
INSERT INTO sales_history VALUES (13, 'ITEM001', TRUNC(SYSDATE) - 1, 19);
INSERT INTO sales_history VALUES (14, 'ITEM001', TRUNC(SYSDATE), 22);

COMMIT;
