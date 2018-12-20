from maq20 import MAQ20

system = MAQ20(ip_address="192.168.1.101", port=502)

#print(help(system))

#print (system.find("S0124418-04"))

#print(system)


m = system.find("VO")
m= system.find("S0124418-04")
m = system.find("S0115278-06")


m=system.find("S0124410-03")
#m=system.find("DORLY20")
#print (help(m))

print(m)


input()
#print(m.write_channel_data(1,True))


#print(m.write_channel_data(0,2.5))
print(m.read_channel_data(10))
print(m.read_channel_data(11))
print(m.read_channel_data(12))


#m2 = system.find("S0115278-06")
#print (m2)
#print(m2.read_channel_data(0))


#print(help(m))

#print(help(system))

#system.scan_module_list()



#print (system.read_system_data)

#m.write_register(1001,1)

print(help(system))

print(system)


while True:
    try:
        pass
        #system.scan_module_list()
        print(system.time())


    except:
        print ("Error")
        pass

# m=system.find("S0120611-14")
   # print(m)



