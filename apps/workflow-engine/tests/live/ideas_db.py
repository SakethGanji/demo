"""Synthetic, isolated data for three generated-app acceptance journeys."""
from dashboard_db import DemoDatabase
from psycopg2 import sql

class IdeasDatabase(DemoDatabase):
 def setup(self):
  super().setup()
  with self.admin() as connection:
   with connection.cursor() as cursor:
    cursor.execute('''
     CREATE TABLE shop_products(sku text PRIMARY KEY,name text NOT NULL,price numeric(10,2) NOT NULL,stock int NOT NULL CHECK(stock>=0));
     CREATE TABLE shop_orders(id text PRIMARY KEY,sku text REFERENCES shop_products,quantity int NOT NULL CHECK(quantity>0),total numeric(10,2) NOT NULL);
     INSERT INTO shop_products VALUES ('MUG','Blue mug',12.50,5),('LAMP','Desk lamp',45,2),('TEST','Test product',7.99,3);
     CREATE TABLE support_tickets(id text PRIMARY KEY,subject text NOT NULL,status text NOT NULL CHECK(status IN ('open','resolved')));
     INSERT INTO support_tickets VALUES ('T-1','Cannot sign in','open'),('T-2','Invoice question','open'),('T-3','Change address','resolved');
     CREATE TABLE expenses(id text PRIMARY KEY,department text,category text,amount numeric(10,2));
     INSERT INTO expenses VALUES ('E1','Engineering','Software',120),('E2','Engineering','Travel',80),('E3','Operations','Software',50),('E4','Operations','Travel',150),('E5','Engineering','Software',30);
    ''')
    cursor.execute(sql.SQL('GRANT SELECT ON shop_products,shop_orders,support_tickets,expenses TO {}').format(sql.Identifier(self.user)))
    cursor.execute(sql.SQL('GRANT UPDATE ON shop_products,support_tickets TO {}').format(sql.Identifier(self.user)))
    cursor.execute(sql.SQL('GRANT INSERT ON shop_orders TO {}').format(sql.Identifier(self.user)))
