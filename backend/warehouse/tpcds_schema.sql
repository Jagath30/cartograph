-- TPC-DS tables as the DuckDB tpcds extension (1.5.5) generates them.
--
-- Written from DuckDB's own catalog, so the column names, order and types
-- are those of the exported CSVs; the load uses COPY ... HEADER MATCH, which
-- refuses a file whose header disagrees with this one.
--
-- No keys of any kind: the extension declares none. Primary keys are derived
-- from tpcds_ri.sql and added after the load; the foreign keys are that file.

create table call_center (
    cc_call_center_sk           bigint,
    cc_call_center_id           varchar,
    cc_rec_start_date           date,
    cc_rec_end_date             date,
    cc_closed_date_sk           bigint,
    cc_open_date_sk             bigint,
    cc_name                     varchar,
    cc_class                    varchar,
    cc_employees                bigint,
    cc_sq_ft                    bigint,
    cc_hours                    varchar,
    cc_manager                  varchar,
    cc_mkt_id                   bigint,
    cc_mkt_class                varchar,
    cc_mkt_desc                 varchar,
    cc_market_manager           varchar,
    cc_division                 bigint,
    cc_division_name            varchar,
    cc_company                  bigint,
    cc_company_name             varchar,
    cc_street_number            varchar,
    cc_street_name              varchar,
    cc_street_type              varchar,
    cc_suite_number             varchar,
    cc_city                     varchar,
    cc_county                   varchar,
    cc_state                    varchar,
    cc_zip                      varchar,
    cc_country                  varchar,
    cc_gmt_offset               decimal(5,2),
    cc_tax_percentage           decimal(5,2)
);

create table catalog_page (
    cp_catalog_page_sk          bigint,
    cp_catalog_page_id          varchar,
    cp_start_date_sk            bigint,
    cp_end_date_sk              bigint,
    cp_department               varchar,
    cp_catalog_number           bigint,
    cp_catalog_page_number      bigint,
    cp_description              varchar,
    cp_type                     varchar
);

create table catalog_returns (
    cr_returned_date_sk         bigint,
    cr_returned_time_sk         bigint,
    cr_item_sk                  bigint,
    cr_refunded_customer_sk     bigint,
    cr_refunded_cdemo_sk        bigint,
    cr_refunded_hdemo_sk        bigint,
    cr_refunded_addr_sk         bigint,
    cr_returning_customer_sk    bigint,
    cr_returning_cdemo_sk       bigint,
    cr_returning_hdemo_sk       bigint,
    cr_returning_addr_sk        bigint,
    cr_call_center_sk           bigint,
    cr_catalog_page_sk          bigint,
    cr_ship_mode_sk             bigint,
    cr_warehouse_sk             bigint,
    cr_reason_sk                bigint,
    cr_order_number             bigint,
    cr_return_quantity          bigint,
    cr_return_amount            decimal(7,2),
    cr_return_tax               decimal(7,2),
    cr_return_amt_inc_tax       decimal(7,2),
    cr_fee                      decimal(7,2),
    cr_return_ship_cost         decimal(7,2),
    cr_refunded_cash            decimal(7,2),
    cr_reversed_charge          decimal(7,2),
    cr_store_credit             decimal(7,2),
    cr_net_loss                 decimal(7,2)
);

create table catalog_sales (
    cs_sold_date_sk             bigint,
    cs_sold_time_sk             bigint,
    cs_ship_date_sk             bigint,
    cs_bill_customer_sk         bigint,
    cs_bill_cdemo_sk            bigint,
    cs_bill_hdemo_sk            bigint,
    cs_bill_addr_sk             bigint,
    cs_ship_customer_sk         bigint,
    cs_ship_cdemo_sk            bigint,
    cs_ship_hdemo_sk            bigint,
    cs_ship_addr_sk             bigint,
    cs_call_center_sk           bigint,
    cs_catalog_page_sk          bigint,
    cs_ship_mode_sk             bigint,
    cs_warehouse_sk             bigint,
    cs_item_sk                  bigint,
    cs_promo_sk                 bigint,
    cs_order_number             bigint,
    cs_quantity                 bigint,
    cs_wholesale_cost           decimal(7,2),
    cs_list_price               decimal(7,2),
    cs_sales_price              decimal(7,2),
    cs_ext_discount_amt         decimal(7,2),
    cs_ext_sales_price          decimal(7,2),
    cs_ext_wholesale_cost       decimal(7,2),
    cs_ext_list_price           decimal(7,2),
    cs_ext_tax                  decimal(7,2),
    cs_coupon_amt               decimal(7,2),
    cs_ext_ship_cost            decimal(7,2),
    cs_net_paid                 decimal(7,2),
    cs_net_paid_inc_tax         decimal(7,2),
    cs_net_paid_inc_ship        decimal(7,2),
    cs_net_paid_inc_ship_tax    decimal(7,2),
    cs_net_profit               decimal(7,2)
);

create table customer (
    c_customer_sk               bigint,
    c_customer_id               varchar,
    c_current_cdemo_sk          bigint,
    c_current_hdemo_sk          bigint,
    c_current_addr_sk           bigint,
    c_first_shipto_date_sk      bigint,
    c_first_sales_date_sk       bigint,
    c_salutation                varchar,
    c_first_name                varchar,
    c_last_name                 varchar,
    c_preferred_cust_flag       varchar,
    c_birth_day                 bigint,
    c_birth_month               bigint,
    c_birth_year                bigint,
    c_birth_country             varchar,
    c_login                     varchar,
    c_email_address             varchar,
    c_last_review_date_sk       integer
);

create table customer_address (
    ca_address_sk               bigint,
    ca_address_id               varchar,
    ca_street_number            varchar,
    ca_street_name              varchar,
    ca_street_type              varchar,
    ca_suite_number             varchar,
    ca_city                     varchar,
    ca_county                   varchar,
    ca_state                    varchar,
    ca_zip                      varchar,
    ca_country                  varchar,
    ca_gmt_offset               decimal(5,2),
    ca_location_type            varchar
);

create table customer_demographics (
    cd_demo_sk                  bigint,
    cd_gender                   varchar,
    cd_marital_status           varchar,
    cd_education_status         varchar,
    cd_purchase_estimate        bigint,
    cd_credit_rating            varchar,
    cd_dep_count                bigint,
    cd_dep_employed_count       bigint,
    cd_dep_college_count        integer
);

create table date_dim (
    d_date_sk                   bigint,
    d_date_id                   varchar,
    d_date                      date,
    d_month_seq                 bigint,
    d_week_seq                  bigint,
    d_quarter_seq               bigint,
    d_year                      bigint,
    d_dow                       bigint,
    d_moy                       bigint,
    d_dom                       bigint,
    d_qoy                       bigint,
    d_fy_year                   bigint,
    d_fy_quarter_seq            bigint,
    d_fy_week_seq               bigint,
    d_day_name                  varchar,
    d_quarter_name              varchar,
    d_holiday                   varchar,
    d_weekend                   varchar,
    d_following_holiday         varchar,
    d_first_dom                 bigint,
    d_last_dom                  bigint,
    d_same_day_ly               bigint,
    d_same_day_lq               bigint,
    d_current_day               varchar,
    d_current_week              varchar,
    d_current_month             varchar,
    d_current_quarter           varchar,
    d_current_year              varchar
);

create table household_demographics (
    hd_demo_sk                  bigint,
    hd_income_band_sk           bigint,
    hd_buy_potential            varchar,
    hd_dep_count                bigint,
    hd_vehicle_count            integer
);

create table income_band (
    ib_income_band_sk           bigint,
    ib_lower_bound              bigint,
    ib_upper_bound              integer
);

create table inventory (
    inv_date_sk                 bigint,
    inv_item_sk                 bigint,
    inv_warehouse_sk            bigint,
    inv_quantity_on_hand        integer
);

create table item (
    i_item_sk                   bigint,
    i_item_id                   varchar,
    i_rec_start_date            date,
    i_rec_end_date              date,
    i_item_desc                 varchar,
    i_current_price             decimal(7,2),
    i_wholesale_cost            decimal(7,2),
    i_brand_id                  bigint,
    i_brand                     varchar,
    i_class_id                  bigint,
    i_class                     varchar,
    i_category_id               bigint,
    i_category                  varchar,
    i_manufact_id               bigint,
    i_manufact                  varchar,
    i_size                      varchar,
    i_formulation               varchar,
    i_color                     varchar,
    i_units                     varchar,
    i_container                 varchar,
    i_manager_id                bigint,
    i_product_name              varchar
);

create table promotion (
    p_promo_sk                  bigint,
    p_promo_id                  varchar,
    p_start_date_sk             bigint,
    p_end_date_sk               bigint,
    p_item_sk                   bigint,
    p_cost                      decimal(15,2),
    p_response_target           bigint,
    p_promo_name                varchar,
    p_channel_dmail             varchar,
    p_channel_email             varchar,
    p_channel_catalog           varchar,
    p_channel_tv                varchar,
    p_channel_radio             varchar,
    p_channel_press             varchar,
    p_channel_event             varchar,
    p_channel_demo              varchar,
    p_channel_details           varchar,
    p_purpose                   varchar,
    p_discount_active           varchar
);

create table reason (
    r_reason_sk                 bigint,
    r_reason_id                 varchar,
    r_reason_desc               varchar
);

create table ship_mode (
    sm_ship_mode_sk             bigint,
    sm_ship_mode_id             varchar,
    sm_type                     varchar,
    sm_code                     varchar,
    sm_carrier                  varchar,
    sm_contract                 varchar
);

create table store (
    s_store_sk                  bigint,
    s_store_id                  varchar,
    s_rec_start_date            date,
    s_rec_end_date              date,
    s_closed_date_sk            bigint,
    s_store_name                varchar,
    s_number_employees          bigint,
    s_floor_space               bigint,
    s_hours                     varchar,
    s_manager                   varchar,
    s_market_id                 bigint,
    s_geography_class           varchar,
    s_market_desc               varchar,
    s_market_manager            varchar,
    s_division_id               bigint,
    s_division_name             varchar,
    s_company_id                bigint,
    s_company_name              varchar,
    s_street_number             varchar,
    s_street_name               varchar,
    s_street_type               varchar,
    s_suite_number              varchar,
    s_city                      varchar,
    s_county                    varchar,
    s_state                     varchar,
    s_zip                       varchar,
    s_country                   varchar,
    s_gmt_offset                decimal(5,2),
    s_tax_percentage            decimal(5,2)
);

create table store_returns (
    sr_returned_date_sk         bigint,
    sr_return_time_sk           bigint,
    sr_item_sk                  bigint,
    sr_customer_sk              bigint,
    sr_cdemo_sk                 bigint,
    sr_hdemo_sk                 bigint,
    sr_addr_sk                  bigint,
    sr_store_sk                 bigint,
    sr_reason_sk                bigint,
    sr_ticket_number            bigint,
    sr_return_quantity          bigint,
    sr_return_amt               decimal(7,2),
    sr_return_tax               decimal(7,2),
    sr_return_amt_inc_tax       decimal(7,2),
    sr_fee                      decimal(7,2),
    sr_return_ship_cost         decimal(7,2),
    sr_refunded_cash            decimal(7,2),
    sr_reversed_charge          decimal(7,2),
    sr_store_credit             decimal(7,2),
    sr_net_loss                 decimal(7,2)
);

create table store_sales (
    ss_sold_date_sk             bigint,
    ss_sold_time_sk             bigint,
    ss_item_sk                  bigint,
    ss_customer_sk              bigint,
    ss_cdemo_sk                 bigint,
    ss_hdemo_sk                 bigint,
    ss_addr_sk                  bigint,
    ss_store_sk                 bigint,
    ss_promo_sk                 bigint,
    ss_ticket_number            bigint,
    ss_quantity                 bigint,
    ss_wholesale_cost           decimal(7,2),
    ss_list_price               decimal(7,2),
    ss_sales_price              decimal(7,2),
    ss_ext_discount_amt         decimal(7,2),
    ss_ext_sales_price          decimal(7,2),
    ss_ext_wholesale_cost       decimal(7,2),
    ss_ext_list_price           decimal(7,2),
    ss_ext_tax                  decimal(7,2),
    ss_coupon_amt               decimal(7,2),
    ss_net_paid                 decimal(7,2),
    ss_net_paid_inc_tax         decimal(7,2),
    ss_net_profit               decimal(7,2)
);

create table time_dim (
    t_time_sk                   bigint,
    t_time_id                   varchar,
    t_time                      bigint,
    t_hour                      bigint,
    t_minute                    bigint,
    t_second                    bigint,
    t_am_pm                     varchar,
    t_shift                     varchar,
    t_sub_shift                 varchar,
    t_meal_time                 varchar
);

create table warehouse (
    w_warehouse_sk              bigint,
    w_warehouse_id              varchar,
    w_warehouse_name            varchar,
    w_warehouse_sq_ft           bigint,
    w_street_number             varchar,
    w_street_name               varchar,
    w_street_type               varchar,
    w_suite_number              varchar,
    w_city                      varchar,
    w_county                    varchar,
    w_state                     varchar,
    w_zip                       varchar,
    w_country                   varchar,
    w_gmt_offset                decimal(5,2)
);

create table web_page (
    wp_web_page_sk              bigint,
    wp_web_page_id              varchar,
    wp_rec_start_date           date,
    wp_rec_end_date             date,
    wp_creation_date_sk         bigint,
    wp_access_date_sk           bigint,
    wp_autogen_flag             varchar,
    wp_customer_sk              bigint,
    wp_url                      varchar,
    wp_type                     varchar,
    wp_char_count               bigint,
    wp_link_count               bigint,
    wp_image_count              bigint,
    wp_max_ad_count             integer
);

create table web_returns (
    wr_returned_date_sk         bigint,
    wr_returned_time_sk         bigint,
    wr_item_sk                  bigint,
    wr_refunded_customer_sk     bigint,
    wr_refunded_cdemo_sk        bigint,
    wr_refunded_hdemo_sk        bigint,
    wr_refunded_addr_sk         bigint,
    wr_returning_customer_sk    bigint,
    wr_returning_cdemo_sk       bigint,
    wr_returning_hdemo_sk       bigint,
    wr_returning_addr_sk        bigint,
    wr_web_page_sk              bigint,
    wr_reason_sk                bigint,
    wr_order_number             bigint,
    wr_return_quantity          bigint,
    wr_return_amt               decimal(7,2),
    wr_return_tax               decimal(7,2),
    wr_return_amt_inc_tax       decimal(7,2),
    wr_fee                      decimal(7,2),
    wr_return_ship_cost         decimal(7,2),
    wr_refunded_cash            decimal(7,2),
    wr_reversed_charge          decimal(7,2),
    wr_account_credit           decimal(7,2),
    wr_net_loss                 decimal(7,2)
);

create table web_sales (
    ws_sold_date_sk             bigint,
    ws_sold_time_sk             bigint,
    ws_ship_date_sk             bigint,
    ws_item_sk                  bigint,
    ws_bill_customer_sk         bigint,
    ws_bill_cdemo_sk            bigint,
    ws_bill_hdemo_sk            bigint,
    ws_bill_addr_sk             bigint,
    ws_ship_customer_sk         bigint,
    ws_ship_cdemo_sk            bigint,
    ws_ship_hdemo_sk            bigint,
    ws_ship_addr_sk             bigint,
    ws_web_page_sk              bigint,
    ws_web_site_sk              bigint,
    ws_ship_mode_sk             bigint,
    ws_warehouse_sk             bigint,
    ws_promo_sk                 bigint,
    ws_order_number             bigint,
    ws_quantity                 bigint,
    ws_wholesale_cost           decimal(7,2),
    ws_list_price               decimal(7,2),
    ws_sales_price              decimal(7,2),
    ws_ext_discount_amt         decimal(7,2),
    ws_ext_sales_price          decimal(7,2),
    ws_ext_wholesale_cost       decimal(7,2),
    ws_ext_list_price           decimal(7,2),
    ws_ext_tax                  decimal(7,2),
    ws_coupon_amt               decimal(7,2),
    ws_ext_ship_cost            decimal(7,2),
    ws_net_paid                 decimal(7,2),
    ws_net_paid_inc_tax         decimal(7,2),
    ws_net_paid_inc_ship        decimal(7,2),
    ws_net_paid_inc_ship_tax    decimal(7,2),
    ws_net_profit               decimal(7,2)
);

create table web_site (
    web_site_sk                 bigint,
    web_site_id                 varchar,
    web_rec_start_date          date,
    web_rec_end_date            date,
    web_name                    varchar,
    web_open_date_sk            bigint,
    web_close_date_sk           bigint,
    web_class                   varchar,
    web_manager                 varchar,
    web_mkt_id                  bigint,
    web_mkt_class               varchar,
    web_mkt_desc                varchar,
    web_market_manager          varchar,
    web_company_id              bigint,
    web_company_name            varchar,
    web_street_number           varchar,
    web_street_name             varchar,
    web_street_type             varchar,
    web_suite_number            varchar,
    web_city                    varchar,
    web_county                  varchar,
    web_state                   varchar,
    web_zip                     varchar,
    web_country                 varchar,
    web_gmt_offset              decimal(5,2),
    web_tax_percentage          decimal(5,2)
);

