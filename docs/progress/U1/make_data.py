import csv, random
random.seed(11)
rows=[{"acct":f"A{i:05d}","zip":f"{random.randint(0,99999):05d}","opened":f"{random.randint(1,12):02d}/{random.randint(1,12):02d}/20{random.randint(15,24)}",
       "income":round(random.uniform(20000,150000),2),"age":random.randint(18,80),"segment":random.choice(["retail","sme","private"]),
       "utilisation":round(random.random(),4),"defaulted":int(random.random()<0.25)} for i in range(1000)]
def w(name):
    f=csv.DictWriter(open(name,"w",newline=""),fieldnames=list(rows[0])); f.writeheader(); f.writerows(rows)
w("pd_accounts.csv")
for i in sorted(random.sample(range(1000),15)): rows[i]["income"]=round(rows[i]["income"]*1.1+1,2)
w("pd_accounts_dec.csv")
for i in sorted(random.sample(range(1000),40)): rows[i]["utilisation"]=round(min(1,rows[i]["utilisation"]+0.05),4)
w("pd_accounts_jan.csv")
