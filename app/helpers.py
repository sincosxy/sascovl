def validate_container_number(number: str) -> bool:
    # Убираем пробелы и тире, приводим к верхнему регистру
    number = number.strip().upper().replace("-", "").replace(" ", "")
    
    if len(number) != 11:
        return False

    # 1. Алфавит для перевода букв в цифры (по стандарту ISO 6346)
    # Обрати внимание: пропущены 11, 22, 33
    char_map = {
        'A': 10, 'B': 12, 'C': 13, 'D': 14, 'E': 15, 'F': 16, 'G': 17, 'H': 18, 'I': 19,
        'J': 20, 'K': 21, 'L': 23, 'M': 24, 'N': 25, 'O': 26, 'P': 27, 'Q': 28, 'R': 29,
        'S': 30, 'T': 31, 'U': 32, 'V': 34, 'W': 35, 'X': 36, 'Y': 37, 'Z': 38
    }

    try:
        sum_val = 0
        # 2. Считаем сумму первых 10 знаков
        for i in range(10):
            char = number[i]
            
            # Если это первые 4 знака (буквы)
            if i < 4:
                val = char_map.get(char)
                if val is None: return False # Ошибка: не буква в префиксе
            # Если это следующие 6 знаков (цифры)
            else:
                if not char.isdigit(): return False
                val = int(char)
            
            # 3. Весовой коэффициент: 2 в степени i
            sum_val += val * (2 ** i)

        # 4. Вычисляем контрольное число
        # Сначала находим остаток от деления на 11
        # По стандарту: делим сумму на 11, отбрасываем дробную часть, умножаем на 11
        check_digit_calculated = sum_val % 11
        
        # Если остаток 10, то контрольное число принимается за 0
        if check_digit_calculated == 10:
            check_digit_calculated = 0
            
        # 5. Сравниваем с последней цифрой номера
        return check_digit_calculated == int(number[10])

    except Exception:
        return False
