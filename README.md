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
#### For Odoo to Tripletex:

- When create a product reference number is required and when select uom for product than in uom common code is require.
- For uom creation in Tripletex, Tripletex expects the commonCode to be a recognized UN/CEFACT unit code.
- If Not valid then uom not create in tripletex.

#### For Tripletex to Odoo:

- When import the product from tripletex check the tripletex number with reference number if not exist than created.
- If uom not in odoo than created under the general category because of creation of uom category is required.


### Customer Sync
#### For Odoo to Tripletex:

- When create a customer than email is require for creation in tripletex because invoice email is require in tripletex.
- When create customer from sale or contacts then create a customer in Tripletex.
- When create vendor from purchase then create a customer/supplier in Tripletex. 
- Import customer from odoo if customer have a trip_customer value.

#### For Tripletex to Odoo:

- Import from Tripletex if customer type is customer and customer/supplier then we should we import using customer and if
  type is supplier then crated in vendor(supplier_rank = 1).

## Usage

- Sync product from Odoo to Tripletex and Tripletex to Odoo.
- Import product from Odoo to Tripletex and Tripletex to Odoo.

---

## Dependencies

### Python library dependencies

- This module have from `BeautifulSoup` python dependencies 
- BeautifulSoup used for web scraping — specifically, for parsing HTML and XML documents.

---

## Limitations, Issues & Bugs

- One product(test) is not deleted in Tripletex so issue raise first time creation on Uom when import the product from Tripletex to Odoo.
- If Common code is not valid then not created uom in Tripletex so we could manually select uom.

- In Supplier sync when create vendor than its created with customer/supplier in Tripletex.
---

## Development

* Inherited `product.template` model
* Inherited method `create` in product.template.
* Inherited method `write` in product.template.
* Inherited method `unlink` in product.template.

---