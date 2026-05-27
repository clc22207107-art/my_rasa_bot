import sqlite3
conn = sqlite3.connect('vending_machine.db')
print('Products:', conn.execute('SELECT COUNT(*) FROM products').fetchone()[0])
print('Inventory:', conn.execute('SELECT COUNT(*) FROM inventory').fetchone()[0])
conn.close()