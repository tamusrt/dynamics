def make_aero(file_index, filename,eng):

    for i in range(31):
        text = ""
        with open(f'aero_files{file_index}/alpha{i}.txt','r') as file:
            text = file.read()
            text = text.replace("                -------->  BODY TRANSITIONING TO TURBULENT FLOW  <--------","")
            text = text.replace("                -------->  FINS TRANSITIONING TO TURBULENT FLOW  <--------","")
            text = text.replace('\n\n\n\n\n','')
            text = text.replace('\n\n\n','')
            
            text = text.replace("                              -------->  FIN LEADING EDGE SUPERSONIC  <--------\n","")
        with open(f'aero_files{file_index}/alpha{i}.txt','w') as file:
            file.write(text)
    print('Making .dat file...', end='')
    if eng.InputFileCreator(filename,f'aero_files{file_index}'):
        print('complete!')