name = input()
cost = int(input())
m = int(input())
money = int(input())

print(f'{"Чек":=^35}\n'
      f'{"Товар:": <6}{name: >29}\n'
      f'{"Цена:": <5}{str(m) + "кг * " + str(cost) + "руб/кг": >30}\n'
      f'{"Итого:": <6}{str(m * cost) + "руб": >29}\n'
      f'{"Внесено:": <8}{str(money) + "руб": >27}\n'
      f'{"Сдача:": <6}{str(money - m * cost) + "руб": >29}\n'
      f'{"":=<35}')