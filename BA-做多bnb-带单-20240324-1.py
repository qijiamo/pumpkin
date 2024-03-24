
import ccxt
import time
import threading
import atexit
from datetime import datetime
import random  # 导入 random 模块
import schedule

# 币安API和秘密

api_key = 'YOUR_API_KEY'
api_secret = 'YOUR_API_SECRET'

# 创建币安交易所实例
exchange = ccxt.binance({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
        'recvWindow': 20000  # 调整此值
    }
})

symbol = 'BNB/USDT'  # 交易对
leverage = 3   # 杠杆倍数
order_qty = 0.3  # 订单数量
price_diff = 0.8  # 价格差
trade_count = 0  # 成交笔数
total_profit = 0  # 总利润
drop_threshold = 0.004   # 下跌或上涨阈值 (10%)
liquidation_threshold = 0.80  # 强平阈值 (75%)
fee_rate = 0.0001  # 假设的手续费率
max_position = 18  # 最大仓位量 (BNB)

min_price = 450    #最低买（卖）单价格
max_price = 650      #最高买（卖）单价格

# 定义仓位方向，做多"long"  做空"short"
position_direction = "long"

# 设置一个小的正数阈值，例如0.0001
threshold = 0.001

# 在全局范围内初始化买入挂单数量，卖出挂单数量
buy_orders_count = 0
sell_orders_count = 0

#更新可交易状态 0为可交易买入
trade_status = 0

# 全局变量用于标记程序是否运行
program_running = True

# 全局变量用于线程同步
lock = threading.Lock()

# 全局变量用于判断下跌标志 1为下跌或上涨
drop_flag = 0
rise_flag = 0

#增加杠杆倍数和只做maker单
params = {
    'timeInForce': 'GTX',       # 1:post_only,只做maker
    'leverage': leverage  # see their docs for more details on parameter names    
}

# 获取初始价格作为第一次交易的基准
orderbook = exchange.fetch_order_book(symbol)
if position_direction == "long":
    last_trade_price = orderbook['bids'][0][0]
elif position_direction == "short":
    last_trade_price = orderbook['asks'][0][0]

def get_current_position():
    """从币安获取当前仓位大小"""
    balance = exchange.fetch_balance()
    positions = balance['info']['positions']
    for pos in positions:
        if pos['symbol'] == 'BNBUSDT':  # 修改为对应的交易对
            position_size = float(pos['positionAmt'])
            return position_size
    return 0

def execute_trade(buy_price):
    global trade_count, total_profit, last_trade_price, buy_orders_count, sell_orders_count,trade_status,drop_flag,max_price,min_price, position_direction,rise_flag,liquidation_threshold

    if position_direction == "short":
        sell_price = buy_price 

    try:
        # 检查是否超过最大仓位
        current_position = get_current_position()
        if abs(current_position) + order_qty > abs(max_position):
            print("多空：总仓位将超过限制，不执行交易")
            drop_flag = 0
            rise_flag = 0
            time.sleep(30)
            return

        if position_direction == "long":  
            # 如果当前买单价格高于最高买单价，则取消并循环
            if buy_price > max_price:
                print(f"多空：当前买单价格高于最高限制买单价,取消并循环等待:buy_price:{buy_price}")
                drop_flag = 0
                time.sleep(30) 
                return
            elif buy_price < min_price:
                print(f"多空：当前买单价格低于最低限制买单价,取消并循环等待:buy_price:{buy_price}")
                drop_flag = 0
                time.sleep(30) 
                return
            
            if buy_orders_count >= 1 and drop_flag == 0:
                # 引入随机等待，等待时间在1到10秒之间  多比卖单或者有买单时控制下速度
                random_wait = random.uniform(1, 10)
                print(f"多空：同时发生购买，分配随机等待时间：{random_wait}秒,buy_orders_count:{buy_orders_count}")
                time.sleep(random_wait)    
            elif drop_flag == 1:
                print(f"多空：下跌买入:buy_orders_count:{buy_orders_count} drop_flag:{drop_flag} buy_price:{buy_price}")
                drop_flag = 0

            # 获取锁
            lock.acquire()
            try:
                while True:
                    try:
                        #再次提取当前价格
                        orderbook = exchange.fetch_order_book(symbol)
                        buy_price = orderbook['bids'][0][0]

                        server_timestamp = exchange.milliseconds()  # 获取服务器时间戳
                        # 以指定价格挂买单
                        buy_order = exchange.create_limit_buy_order(symbol, order_qty, buy_price,params)
                        buy_fee = buy_price * order_qty * fee_rate
                        #判断挂买单是否成功
                        if buy_order:                                       
                            # 跟踪买入挂单数量
                            buy_orders_count += 1
                            print(f"挂买单成功：buy_price{buy_price},买入挂单：{buy_orders_count},orderid:{buy_order['id']}") 
                            #更新可交易状态 0为可交易买入
                            trade_status = 1
                            break
                        else:
                            print("挂买单失败：",buy_price)
                        time.sleep(2)
                    except Exception as e:
                        print("挂买单出错，重新执行:", e)
                        continue
            finally:
                # 释放锁
                lock.release()

            # 设置买入超时时间（秒）
            trade_timeout = 60  # 1分钟

            # 等待买单成交或超时
            start_time = time.time()
            while True:
                try:
                    order_status = exchange.fetch_order(buy_order['id'], symbol)['status']
                    if order_status == 'closed':
                        # 跟踪买入挂单数量
                        buy_orders_count -= 1
                        last_trade_price = buy_price  #从上面挪到这里
                        print(f"买入成功：buy_price{buy_price},买入挂单：{buy_orders_count}")
                        time.sleep(random.uniform(1, 10))
                        break
                    elif time.time() - start_time > trade_timeout:
                        # 超时取消买单
                        exchange.cancel_order(buy_order['id'], symbol)
                        buy_orders_count -= 1
                        print(f"买入超时取消：{buy_price}买入挂单：{buy_orders_count}")
                        #更新可交易状态 0为可交易买入
                        trade_status = 0
                        return
                    time.sleep(2)
                except Exception as e:
                        print(f"买单判断出错，重新执行:{e},order:{buy_order['id']}")
                        time.sleep(5)
                        continue
            
            while True:
                try:
                    # 提取卖一价
                    sell_price = orderbook['asks'][0][0]
                    # 比较卖一价和挂单价格的最大值，计算卖出价格并挂卖单
                    sell_price = max(sell_price, buy_price + price_diff)
                    server_timestamp = exchange.milliseconds()  # 获取服务器时间戳
                    sell_order = exchange.create_limit_sell_order(symbol, order_qty, sell_price, params)
                    sell_fee = sell_price * order_qty * fee_rate
                    #判断卖单是否成功
                    if sell_order:
                        # 跟踪卖出挂单数量
                        sell_orders_count += 1
                        # 打印跟踪日志
                        print(f"卖出挂单成功:{sell_price},sell_orders_count:{sell_orders_count}")
                        break
                    else:
                        print("卖出挂单失败：",sell_price)
                    time.sleep(2)
                except Exception as e:
                    print("卖出挂单出错，重新执行:", e)
                    continue

            # 等待卖单成交
            while True:
                try:
                    order_status = exchange.fetch_order(sell_order['id'], symbol)['status']
                    if order_status == 'closed':
                        # 减少卖出挂单数量
                        print(f"卖出成功:{sell_price},sell_orders_count:{sell_orders_count}")
                        sell_orders_count -= 1
                        print(f"卖出成功:{sell_price},sell_orders_count:{sell_orders_count}")
                        time.sleep( random.uniform(1, 10) )
                        break  
                    time.sleep(2)
                except Exception as e:
                    print("卖出成交判断出错，重新执行:", e)
                    continue

        elif position_direction == "short":   #做空
            # 如果当前卖单价格高于最高卖单价，则取消并循环
            if sell_price > max_price:
                print(f"多空：当前卖单价格高于最高限制卖单价,取消并循环等待:{sell_price}")
                rise_flag = 0
                time.sleep(30) 
                return
            elif sell_price < min_price:
                print(f"多空：当前卖单价格低于最低限制卖单价,取消并循环等待:{sell_price}")
                rise_flag = 0
                time.sleep(30) 
                return
            
            if sell_orders_count >= 1 and rise_flag == 0:
                # 引入随机等待，等待时间在1到10秒之间  多比卖单或者有买单时控制下速度
                random_wait = random.uniform(1, 10)
                print(f"多空：同时发生卖出，分配随机等待时间：{random_wait}秒,sell_orders_count:{sell_orders_count}")
                time.sleep(random_wait)
            elif rise_flag == 1:
                print(f"多空：上涨卖出:buy_orders_count:{buy_orders_count} rise_flag:{rise_flag} price:{sell_price}")
                rise_flag = 0

            # 获取锁
            lock.acquire()
            try:
                while True:
                    try:
                        #再次提取当前卖出价格
                        orderbook = exchange.fetch_order_book(symbol)
                        sell_price = orderbook['asks'][0][0]
                        server_timestamp = exchange.milliseconds()  # 获取服务器时间戳
                        # 以指定价格挂卖单
                        sell_order = exchange.create_limit_sell_order(symbol, order_qty, sell_price,params)
                        sell_fee = sell_price * order_qty * fee_rate
                        #判断卖单是否成功
                        if sell_order:
                            print("挂卖单成功：",sell_price)
                            # 跟踪卖出挂单数量
                            sell_orders_count += 1
                            break
                        else:
                            print("挂卖单失败：",sell_price)
                        
                        time.sleep(2)
                    except Exception as e:
                        print("卖出挂单出错，重新执行:", e)
                        continue
                
                #更新可交易状态 0为可交易买入
                trade_status = 1
                time.sleep(2)
            finally:
                # 释放锁
                lock.release()

            # 设置卖出超时时间（秒）
            trade_timeout = 6000  # 100分钟  #根据实际交易对调整等待时间  正常交易时间不会超过10分钟

            # 等待卖单成交或超时
            start_time = time.time()
            while True:
                try:
                    order_status = exchange.fetch_order(sell_order['id'], symbol)['status']
                    if order_status == 'closed':
                        # 跟踪卖出挂单数量
                        sell_orders_count -= 1
                        last_trade_price = sell_price  #从上面挪到这里
                        print("卖出成功：",sell_price)
                        time.sleep(random.uniform(1, 10))
                        break
                    elif time.time() - start_time > trade_timeout:
                        # 超时取消卖单
                        exchange.cancel_order(sell_order['id'], symbol)
                        sell_orders_count -= 1
                        print("卖出超时取消：", sell_price)
                        #更新可交易状态 0为可交易买入
                        trade_status = 0
                        return
                    time.sleep(2)    # 根据实际交易对调整等待时间 正常交易时间不会超过30秒
                except Exception as e:
                    print("卖出成交判断出错，重新执行:", e)
                    time.sleep(5)
                    continue
            
            while True:
                try:
                    # 提取买一价
                    buy_price = orderbook['bids'][0][0]
                    # 比较买一价和挂单价格的最大值，计算买入价格并挂买单
                    buy_price = min(buy_price, sell_price - price_diff)
                    server_timestamp = exchange.milliseconds()  # 获取服务器时间戳
                    buy_order = exchange.create_limit_buy_order(symbol, order_qty, buy_price,params)
                    buy_fee = buy_price * order_qty * fee_rate
                    if buy_order:
                        # 跟踪买入挂单数量
                        buy_orders_count += 1
                        # 打印跟踪日志
                        print(f"买入挂单成功:{buy_price},buy_orders_count:{buy_orders_count}")
                        break
                    else:
                        print("挂买单失败：",buy_price)
                    time.sleep(2)
                except Exception as e:
                    print("买入挂单出错，重新执行:", e)
                    continue
                
            # 等待买单成交
            while True:
                try:
                    order_status = exchange.fetch_order(buy_order['id'], symbol)['status']
                    if order_status == 'closed':
                        # 减少买入挂单数量
                        buy_orders_count -= 1
                        print("买入成功：",buy_price)
                        time.sleep(random.uniform(1, 10))
                        break  
                    time.sleep(5)   # 根据实际交易对调整等待时间 正常交易时间不会超过30秒                
                except Exception as e:
                    print("买入成交判断出错，重新执行:", e)
                    time.sleep(2)
                    continue

        # 更新配对次数、总利润和上次交易价格
        trade_count += 1
        profit = (sell_price - buy_price) * order_qty - buy_fee - sell_fee
        total_profit += profit
        #更新可交易状态 0为可交易买入
        trade_status = 0
        
    except Exception as e:
        print("交易执行中发生错误:", e)
        time.sleep(10)

def trade_logic():
    global program_running,drop_flag,trade_status,rise_flag,position_direction

    if position_direction == "long":
        sell_count1 = sell_orders_count
        time.sleep(2) 
        if sell_count1 - sell_orders_count > 0:
            print(f"当前同时成交多个卖单，结束该循环:当前卖单：{sell_orders_count},2秒前卖单:{sell_count1}")
            return
    elif position_direction == "short":
        buy_count1 = buy_orders_count
        time.sleep(2) 
        if buy_count1 - buy_orders_count > 0:
            print(f"当前同时成交多个买单，结束该循环:当前买单：{buy_orders_count},2秒前买单:{buy_count1}")
            return
    
    while program_running:
        try:
            print(f"多空循环开始000:buy_orders_count:{buy_orders_count}  sell_orders_count:{sell_orders_count}  trade_status:{trade_status}  drop_flag:{drop_flag}")
            # 获取当前市场价格
            orderbook = exchange.fetch_order_book(symbol)
            if position_direction == "long":
                # 提取买单价格
                current_price = orderbook['bids'][0][0]
                sell_price = current_price + price_diff

                # 计算手续费和利润
                buy_fee = current_price * order_qty * fee_rate
                sell_fee = sell_price * order_qty * fee_rate
                profit = (sell_price - current_price) * order_qty - buy_fee - sell_fee

                if buy_orders_count > 1 and drop_flag == 0:
                    print("当前已有买单，结束该循环：",buy_orders_count)
                    return
            
                # 如果预计利润大于0，则执行交易
                #print("current_price:",current_price,"bb:",last_trade_price * (1 - drop_threshold))
                if profit <= threshold:
                    print("无利润可赚，退出：",profit)
                    return
                if buy_orders_count == 0 and sell_orders_count == 0 and profit > threshold:
                    print("无买卖单开始交易")
                    execute_trade(current_price)
                elif drop_flag  == 1:
                    print("下跌开始增加交易")
                    execute_trade(current_price)
                elif trade_status == 0 and profit > threshold:
                    print("买单取消,开始交易")
                    execute_trade(current_price)
                elif buy_orders_count == 1:
                    print("允许有两个买单，开始交易")
                    execute_trade(current_price)
                else:
                    print("未达到可交易条件，退出！")
                    return
            elif position_direction == "short":    # 做空
                # 提取卖单价格
                current_price = orderbook['asks'][0][0]
                buy_price = current_price - price_diff

                # 计算手续费和利润
                sell_fee = current_price * order_qty * fee_rate
                buy_fee = buy_price * order_qty * fee_rate
                #profit = (sell_price - current_price) * order_qty - buy_fee - sell_fee
                profit = (current_price - buy_price) * order_qty - buy_fee - sell_fee

                if sell_orders_count > 1 and rise_flag == 0:
                    print("当前已有卖单，结束该循环：",sell_orders_count)
                    return
            
                # 如果预计利润大于0，则执行交易
                #print("current_price:",current_price,"bb:",last_trade_price * (1 - drop_threshold))
                if profit <= threshold:
                    print("无利润可赚，退出：",profit)
                    return
                if buy_orders_count == 0 and sell_orders_count == 0 and profit > threshold:
                    print("无买卖单开始交易")
                    execute_trade(current_price)
                elif rise_flag  == 1:
                    print("上涨开始增加交易")
                    execute_trade(current_price)
                elif trade_status == 0 and profit > threshold:
                    print("卖单取消,开始交易")
                    execute_trade(current_price)
                elif sell_orders_count == 1:
                    print("允许有两个卖单，开始交易")
                    execute_trade(current_price)
                else:
                    print("未达到可交易条件，退出！")
                    return
            time.sleep(10)  # 短暂休息，防止过快调用API

        except ccxt.NetworkError as e:
            print("网络错误:", e)
            time.sleep(30)  # 短暂休息
        except ccxt.ExchangeError as e:
            print("交易所错误:", e)
            time.sleep(30)  # 短暂休息
        except Exception as e:
            print("发生错误:", e)
            time.sleep(30)  # 短暂休息

def print_info():
    global program_running, position_direction
    while program_running:
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            balance = exchange.fetch_balance()
            usdt_balance = balance['total']['USDT']
            current_position = get_current_position()  # 获取当前仓位

            print(f"多空合约：交易对：{symbol},时间: {current_time}, 仓位方向:{position_direction}, 配对笔数: {trade_count}, 账户余额: {usdt_balance}, 总利润: {total_profit}, 当前仓位: {current_position} BNB, 最大仓位: {max_position}, 最低价格: {min_price}, 最高价格: {max_price}, 当前买入挂单数：{buy_orders_count}, 当前卖出挂单数：{sell_orders_count},下单数量: {order_qty}")
            time.sleep(60)

        except Exception as e:
            print("打印信息时发生错误:", e)
            time.sleep(60)  # 如果发生异常，继续等待
        
# 定时器函数，用于检查下跌条件
def check_drop_condition(interval):
    global program_running, drop_flag,rise_flag, position_direction
    while program_running:
        try:
            if position_direction == "long":
                # 在此处添加下跌条件的检查
                orderbook = exchange.fetch_order_book(symbol)
                current_price = orderbook['bids'][0][0]
                print(f"多空下跌检查:最后买价 {last_trade_price}, 当前价格: {current_price}, 跌幅价格: {last_trade_price * (1 - drop_threshold)}")
                if current_price < last_trade_price * (1 - drop_threshold) and buy_orders_count == 0:
                    print("下跌超过阈值，触发下一个循环")
                    drop_flag = 1
                    threading.Thread(target=trade_logic).start()
            elif position_direction == "short":
                # 在此处添加上涨条件的检查
                orderbook = exchange.fetch_order_book(symbol)
                current_price = orderbook['asks'][0][0]
                print(f"多空上涨检查:最后交易价格 {last_trade_price}, 当前价格: {current_price}, 涨幅价格: {last_trade_price * (1 + drop_threshold)}")
                if current_price > last_trade_price * (1 + drop_threshold) and sell_orders_count == 0:
                    print("上涨超过阈值，触发下一个循环")
                    rise_flag = 1
                    threading.Thread(target=trade_logic).start()
            time.sleep(interval)  # 使用传入的时间间隔进行检查

        except Exception as e:
            print("调用下跌检查时发生错误:", e)
        finally:
            time.sleep(interval)

def sync_server_time():
    while True:
        try:
            # 获取服务器时间戳
            #server_timestamp = datetime.fromtimestamp(exchange.fetch_time()/1000)
            server_timestamp = exchange.fetch_time()
            # 获取本地时间戳
            local_timestamp = int(time.time() * 1000)
            
            # 计算时间戳的差值
            time_offset = server_timestamp - local_timestamp
            print(f"调整前：本地时间戳: {local_timestamp}，服务器时间戳: {server_timestamp}, 时间戳偏移量: {time_offset} 毫秒")

            # 设置时间戳的偏移量
            #exchange.options['adjustForTimeDifference'] = True
            #exchange.rateLimit = 3000  # 设置请求速率限制
            
            adjusted_time = time.time() + (time_offset / 1000)
            time.localtime(adjusted_time)
            print(f"增加：{adjusted_time*1000}，调整后：本地时间戳: {int(time.time() * 1000)}，服务器时间戳: {exchange.fetch_time()}，时间戳偏移量: {exchange.fetch_time()-int(time.time() * 1000)} 毫秒")
            time.sleep(3600)  # 每1小时同步一次
        except Exception as e:
            print(f"多空：同步服务器时间出错: {e}")
            time.sleep(1)

# 开始定时同步服务器时间
sync_thread = threading.Thread(target=sync_server_time)
sync_thread.start()

# 创建并启动交易线程
trade_thread = threading.Thread(target=trade_logic)
trade_thread.start()

# 创建并启动信息打印线程
print_thread = threading.Thread(target=print_info)
print_thread.start()

def exit_handler():
    global program_running
    print("Exiting...")
    program_running = False

# 注册退出处理函数
atexit.register(exit_handler)

# 创建并启动定时器线程，每个线程具有不同的时间间隔
timer_thread1 = threading.Thread(target=check_drop_condition, args=(10,))  # 时间间隔为10秒
timer_thread1.start()


# 主线程等待检查下跌条件的线程完成
timer_thread1.join()

# 主线程等待其他线程完成
trade_thread.join()
print_thread.join()
sync_thread.join()

