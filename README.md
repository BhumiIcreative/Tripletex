# Tripletex

Sync Data

**Table of Contents**

- Configuration
- Usage
- Dependencies
- Issues & Bugs
- Development

---

## Configuration

### Product Sync
#### Basic Information

- Synced only those fields with Tripletex that are available in the Tripletex.
- Field which is sync
  - Name -> Name
  - Reference -> product number
  - UOM -> Product Unit
  - Internal notes -> Product Description
  - Sale Orderline Description -> Orderline Description
  - Cost Price -> Purchase Price
  - Selling -> Selling Price
  - Vendor -> Suppliers
  - Active -> inactive

#### Create & Write & Unlink

- When creating a product, ensure it's created in Tripletex with the necessary data required for invoicing.
- When create a product reference number is required and for product uom common code is require.
- Ensured each common code maps to only one UoM.
- some common code which is support tripletex(aa,bb,qq,ww)
- For uom creation in Tripletex, Tripletex expects the common code to be a recognized UN/CEFACT unit code.
- If Not valid then uom not create in tripletex.
- And When update the data then all data which is in tripletex that is updated.
- When a product is deleted in Odoo, it is also deleted from Tripletex.
- Synced vendor in product with Tripletex only if the selected vendor has valid Tripletex-compatible data.


#### product sync to odoo

- For Update the data click Product Sync to Odoo button from
  - General Setting -> Tripletex Authentication menu -> Product Sync -> Product Sync to Odoo
- for update the product in tripletex product id and default code is required.
- And product have a resale product (supplier in tripletex) then update the vendor in odoo in purchase tab.
- When multiple vendors are linked to a product in odoo, the newly synced vendor from Tripletex is always placed first in seller_ids.

#### import Odoo to Tripletex:

- For Import the data click Import Product From Tripletex button from
  - General Setting -> Tripletex Authentication menu -> Product Sync -> Import Product From Tripletex.
- When a product already exists in Odoo, import it into Tripletex and reuse the existing product data for syncing. 
- If the UoM has a valid common code and the vendor has valid data otherwise, they are not synced and left empty.
- While importing products, check if the product reference number is not already available as a product number in Tripletex; if not, import the product.

#### import Tripletex to Odoo:

- For Import the data click Import Product From Odoo button from
  - General Setting -> Tripletex Authentication menu -> Product Sync -> Import Product From Odoo.
- When a product already exists in Tripletex, import it into Odoo and reuse the existing product data for syncing.
- When import the product from tripletex check the tripletex number with reference number if not exist than created.
- If uom not in odoo than created under the general category because of creation of uom category is required and common code update with Tripletex product uom common code.
- If supplier is selected in the product then sync in product else not sync.
- While importing products, check if the product number in Tripletex is not already available as a product reference number in odoo; if not, import the product.

---

### Customer Sync
#### Basic Information

- Synced only those fields with Tripletex that are available in the Tripletex.
- Field which is sync 
  - Name -> Name
  - Email -> Email
  - Alternative email -> Alternative email
  - Tax ID -> Organization number
  - Phone -> Telephone
  - Mobile -> Mobile Phone
  - Website -> Website
  - Customer Address -> Postal Address
  - Business Address -> Business Address
  - Due Days -> Due date
  - Discount Chargeable Order line -> Discount Chargeable Order line
  - Single customer invoice -> Single customer invoice
  - Soft reminder -> Soft reminder
  - Reminder -> Reminder
  - Notice of debt collection -> Notice of debt collection
  - Invoice Send Method -> Invoice Send Method
  - Send Invoice By Email as -> Send Invoice By Email as
  - Invoice Due Type -> Invoice Due Type
  
#### Create & Write & Unlink

- When create a customer than email is require for creation in tripletex because invoice email is require in tripletex.
- When create customer from sale then create a customer in Tripletex.
- 
#### Customer Sync to Odoo

- For Update the data click Customer Sync to Odoo Button from 
  - General Setting -> Tripletex Authentication menu -> Customer Sync -> Customer Sync to Odoo
- When trip_customer (In odoo res.partner) and tripletex customer id is same then update into the odoo.

#### Import Odoo to Tripletex:

- For Update the data click Import Customers From Odoo Button from 
  - General Setting -> Tripletex Authentication menu -> Customer Sync -> Import Customers From Odoo
- Import customer from odoo if customer_rank >= 1.
- If Customer have email.

#### For Tripletex to Odoo:

- For Update the data click Import Customers From Tripletex Button from 
  - General Setting -> Tripletex Authentication menu -> Customer Sync -> Import Customers From Tripletex
- Import from Tripletex if customer type is customer and customer/supplier then we should we import using customer and if
  type is supplier then crated in vendor(supplier_rank = 1).


### Supplier Sync
#### Basic Information

- Synced only those fields with Tripletex that are available in the Tripletex.
- Field which is sync 
  - Name -> Name
  - Email -> Email
  - Alternative email -> Alternative email
  - Tax ID -> Organization number
  - Phone -> Telephone
  - Mobile -> Mobile Phone
  - Website -> Website
  - Supplier Address -> Postal Address
  - Business Address -> Business Address
  
#### Create & Write & Unlink

- When create a Supplier than email is require for creation in tripletex because invoice email is require in tripletex.
- When create Supplier from Purchase then create a Supplier in Tripletex.
- 
#### Supplier Sync to Odoo

- For Update the data click Supplier Sync to Odoo Button from 
  - General Setting -> Tripletex Authentication menu -> Supplier Sync -> Supplier Sync to Odoo
- When trip_customer (In odoo res.partner) and tripletex Supplier id is same then update into the odoo.

#### Import Odoo to Tripletex:

- For Update the data click Import Supplier From Odoo Button from 
  - General Setting -> Tripletex Authentication menu -> Supplier Sync -> Import Supplier From Odoo
- Import Supplier from odoo if supplier_rank >= 1.

#### For Tripletex to Odoo:

- For Update the data click Import Supplier From Tripletex Button from 
  - General Setting -> Tripletex Authentication menu -> Supplier Sync -> Import Supplier From Tripletex
- Import from Tripletex if Customer type is Supplier and crated in vendor(supplier_rank = 1).

### Invoice Sync
#### Basic Information

- When conform the invoice from odoo then create in the tripletex.
- A custom text field is added on the invoice line to override the product description.
- If the custom text field is empty, the product description should be used instead. 

## Usage

- Sync product from Odoo to Tripletex and Tripletex to Odoo.
- Import product from Odoo to Tripletex and Tripletex to Odoo.

---

## Dependencies

### Python library dependencies

- This module have from `BeautifulSoup` python dependencies 
- BeautifulSoup used for web scraping — specifically, for parsing HTML and XML documents.

