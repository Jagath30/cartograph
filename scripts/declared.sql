-- The five hand-declared relationships of backend/overlays/tpcds.yaml,
-- measured against their notes (item 41, step 14). Read-only: SELECTs
-- only, run as the warehouse's reader. Each line is one fact a note
-- states; the notes were measured at scale factor 1.
--
--     ./scripts/neon.sh declared     on Neon (sf=0.25)
--     docker compose exec -T postgres sh -c 'PGPASSWORD="$WAREHOUSE_RO_PASSWORD" psql -X -At -U "$WAREHOUSE_RO_USER" \
--       -d "$WAREHOUSE_DB_NAME" -h 127.0.0.1' < scripts/declared.sql      locally (sf=1)

\set ON_ERROR_STOP 1

-- 1. customer.c_last_review_date_sk -> date_dim.d_date_sk
select '1 c_last_review_date_sk  rows ' || count(*) || ', non-null ' || count(c_last_review_date_sk)
       || ', distinct ' || count(distinct c_last_review_date_sk) from customer;
select '1 c_last_review_date_sk  dates ' || min(d.d_date) || ' to ' || max(d.d_date)
       || ', contiguous ' || ((max(c.c_last_review_date_sk) - min(c.c_last_review_date_sk) + 1)
                              = count(distinct c.c_last_review_date_sk))
from customer c join date_dim d on d.d_date_sk = c.c_last_review_date_sk;
select '1 c_last_review_date_sk  violations ' || count(*)
from customer c where c.c_last_review_date_sk is not null
  and not exists (select 1 from date_dim d where d.d_date_sk = c.c_last_review_date_sk);

-- 2. web_page.wp_customer_sk -> customer.c_customer_sk
select '2 wp_customer_sk  rows ' || count(*) || ', non-null ' || count(wp_customer_sk) from web_page;
select '2 wp_customer_sk  violations ' || count(*)
from web_page w where w.wp_customer_sk is not null
  and not exists (select 1 from customer c where c.c_customer_sk = w.wp_customer_sk);
select '2 wp_customer_sk  customers ' || count(*) || ', keys 1 to ' || max(c_customer_sk)
       || ', every integer a key ' || (count(*) = max(c_customer_sk) and min(c_customer_sk) = 1) from customer;

-- 3. (sr_item_sk, sr_ticket_number) -> (ss_item_sk, ss_ticket_number)
select '3 store  sales ' || count(*) || ', pair null ' || count(*) filter (where ss_item_sk is null or ss_ticket_number is null)
from store_sales;
select '3 store  sales pairs repeated ' || count(*)
from (select 1 from store_sales group by ss_item_sk, ss_ticket_number having count(*) > 1) repeated;
select '3 store  returns ' || count(*) from store_returns;
select '3 store  returns matching exactly one sale ' || count(*)
from (select r.sr_item_sk, r.sr_ticket_number from store_returns r
      join store_sales s on s.ss_item_sk = r.sr_item_sk and s.ss_ticket_number = r.sr_ticket_number
      group by r.sr_item_sk, r.sr_ticket_number having count(*) = 1) one;
select '3 store  violations ' || count(*)
from store_returns r where not exists (
  select 1 from store_sales s where s.ss_item_sk = r.sr_item_sk and s.ss_ticket_number = r.sr_ticket_number);
select '3 store  returns pairs repeated ' || count(*)
from (select 1 from store_returns group by sr_item_sk, sr_ticket_number having count(*) > 1) repeated;

-- 4. (cr_item_sk, cr_order_number) -> (cs_item_sk, cs_order_number)
select '4 catalog  sales ' || count(*) || ', pair null ' || count(*) filter (where cs_item_sk is null or cs_order_number is null)
from catalog_sales;
select '4 catalog  sales pairs repeated ' || count(*)
from (select 1 from catalog_sales group by cs_item_sk, cs_order_number having count(*) > 1) repeated;
select '4 catalog  returns ' || count(*) from catalog_returns;
select '4 catalog  returns matching exactly one sale ' || count(*)
from (select r.cr_item_sk, r.cr_order_number from catalog_returns r
      join catalog_sales s on s.cs_item_sk = r.cr_item_sk and s.cs_order_number = r.cr_order_number
      group by r.cr_item_sk, r.cr_order_number having count(*) = 1) one;
select '4 catalog  violations ' || count(*)
from catalog_returns r where not exists (
  select 1 from catalog_sales s where s.cs_item_sk = r.cr_item_sk and s.cs_order_number = r.cr_order_number);
select '4 catalog  returns pairs repeated ' || count(*)
from (select 1 from catalog_returns group by cr_item_sk, cr_order_number having count(*) > 1) repeated;

-- 5. (wr_item_sk, wr_order_number) -> (ws_item_sk, ws_order_number)
select '5 web  sales ' || count(*) || ', pair null ' || count(*) filter (where ws_item_sk is null or ws_order_number is null)
from web_sales;
select '5 web  sales pairs repeated ' || count(*)
from (select 1 from web_sales group by ws_item_sk, ws_order_number having count(*) > 1) repeated;
select '5 web  returns ' || count(*) from web_returns;
select '5 web  returns matching exactly one sale ' || count(*)
from (select r.wr_item_sk, r.wr_order_number from web_returns r
      join web_sales s on s.ws_item_sk = r.wr_item_sk and s.ws_order_number = r.wr_order_number
      group by r.wr_item_sk, r.wr_order_number having count(*) = 1) one;
select '5 web  violations ' || count(*)
from web_returns r where not exists (
  select 1 from web_sales s where s.ws_item_sk = r.wr_item_sk and s.ws_order_number = r.wr_order_number);
select '5 web  returns pairs repeated ' || count(*)
from (select 1 from web_returns group by wr_item_sk, wr_order_number having count(*) > 1) repeated;
