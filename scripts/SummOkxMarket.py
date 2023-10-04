'''
Created on 2023年6月8日

基于bolling带进行趋势操作


@author: wfeng007
'''
from typing import Any, Dict, List, Set
from collections import deque
import requests
import logging

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.data_type.common import PriceType, OrderType, TradeType
from hummingbot.core.data_type.limit_order import LimitOrder
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent, BuyOrderCreatedEvent, \
    SellOrderCreatedEvent, OrderCancelledEvent
    

from .summ_hbot_okx_utilz import getCandlesDf

from decimal import Decimal
# import pandas as pd



class SummOkxMarket(ScriptStrategyBase):
    '''
    获取市场信息形成指标线；信息使用candle K线蜡烛图；
    基于指标线进行交易操作；
    '''
    # exchange="okx"
    BASE_ASSET="ETH"
    QUOTE_ASSET="USDT"
    EXCHANGE_NAME="okx" #OK交易所
    
    #量
    order_amount = 0.003
    #刷新周期
    order_refresh_time = 600 #s
    #类型
    price_source = PriceType.MidPrice
    #
    trading_pair = f"{BASE_ASSET}-{QUOTE_ASSET}"
    # exchange = "binance_paper_trade"
    exchange = EXCHANGE_NAME
    
     #框架逻辑会基于markets进行connector初始化等；
    markets={
        
        # "binance_paper_trade": {"BTC-USDT","ETH-USDT"},
        # "kucoin_paper_trade": {"ETH-USDT"},
        # "gate_io_paper_trade": {"ETH-USDT"} 
        exchange: {trading_pair} #okx是正式的exchange
    }
    
    STRATEGY_INFO=f"SummOkxMarket v20230608"

    def __init__(self,connectors: Dict[str, ConnectorBase]):
        '''
        Constructor
        '''
        super().__init__(connectors)
        
    # de_fast_ma = deque([], maxlen=5)
    # de_slow_ma = deque([], maxlen=20)
    
    #均线信息：
    std5mQue= deque([], maxlen=10)
    std20mQue= deque([], maxlen=10)
    
    #布林带信息：
    bollUBQue=deque([], maxlen=10) #上带
    bollSmaQue= deque([], maxlen=10)
    bollLBQue= deque([], maxlen=10)#下带
    
    currentCandlesDf=None
    
    order_refresh_time = 11
    #本实例中的 创建tick时间戳
    create_timestamp = 0

    #趋势策略，持仓标志,<0空头仓 >0多头仓 0无仓位 #TODO 这里因为现货用简单仓位标志来记录仓位；
    position_mark=0
    liqQue=deque([], maxlen=10)
    isOrderTracked=False
    
    def on_tick(self):
        
        # 获取info信息
        # for connectorName,connector in self.connectors.items():
        #     self.logger().info(f"Connector:{connectorName}")
        #     self.logger().info(f"Best ask:{connector.get_price('ETH-USDT',True)}") #卖
        #     self.logger().info(f"Best bid:{connector.get_price('ETH-USDT',False)}") #买
        #     self.logger().info(f"Mid price:{connector.get_mid_price('ETH-USDT')}") #中价
        if self.create_timestamp <= self.current_timestamp:
            try:
                candlesDf=getCandlesDf(pairName=f"{self.BASE_ASSET}-{self.QUOTE_ASSET}", period="1m",limit=21)
                self.currentCandlesDf=candlesDf 
                
                #
                # 计算5分钟与20分钟移动平均线；
                #
                #可以错开时间执行下面： @FIXME 波动率(波幅)最好用hightest lowest 而不是close
                curStd5m=candlesDf.iloc[1:5]['close'].std() #5分钟价格标准差 含有当前分钟
                #@FIXME:其实需要用时间戳判断是否有变化，这里临时用数值直接判断
                if len(self.std5mQue)<=0 or abs(self.std5mQue[-1]-curStd5m)>0.000001: #这里浮点数比较是否相同，不同才写入
                    self.std5mQue.append(curStd5m) #右侧，list表最后
                curStd20m=candlesDf.iloc[1:20]['close'].std() #标准差
                if len(self.std20mQue)<=0 or abs(self.std20mQue[-1]-curStd20m)>0.000001:
                    self.std20mQue.append(curStd20m)
                
                #
                # 布林带；
                # 以收盘价建立当前布林带信息
                # 提取收盘价
                closes = [float(closeP) for closeP in self.currentCandlesDf['close']]
                
                upper_band, sma, lower_band=self.calc_bollinger_bands(closes,n=20 if len(closes)>= 20 else len(closes) )
                #@FIXME 有变化则塞入Que，其实应该用时间戳的；
                if len(self.bollUBQue)<=0 or abs(self.bollUBQue[-1]-upper_band)>0.000001:
                    self.bollUBQue.append(upper_band)
                if len(self.bollSmaQue)<=0 or abs(self.bollSmaQue[-1]-sma)>0.000001:
                    self.bollSmaQue.append(sma)
                if len(self.bollLBQue)<=0 or abs(self.bollLBQue[-1]-lower_band)>0.000001:
                    self.bollLBQue.append(lower_band)

                # self.log_with_clock(logging.INFO, f"bolling is ok!") 调试

                # 需要扩展的部分；
                #
                proposalLs:List[OrderCandidate] = []
                ref_price = self.connectors[self.exchange]\
                    .get_price_by_type(self.trading_pair, self.price_source) #注意ref_price 这里得到是Decimal 类型
                #
                #@TODO 入场过滤器，离场自适应均线
                #
                #
                rocLen=10 #入场过滤器参数
                liqLen=20 #离场线参数；可以跟boll中线参数一致

                #@TODO 入场过滤器计算，之后替换为更复杂的入场信号；
                rocThre=float(ref_price)-closes[-rocLen] #计算过滤器值，正负方向，大小为强度；
                #
                #开仓逻辑与信号处理
                #
                #无多头持仓，且roc过滤器为正，突破上线，开多仓；
                if self.position_mark == 0 and rocThre>0 and ref_price>upper_band:
                    buy_price=ref_price
                    buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                       order_side=TradeType.BUY, amount=Decimal(self.order_amount), price=buy_price)
                    proposalLs.append(buy_order)
                
                #无空头仓，且roc过滤器为负，突破下线，开空仓；
                elif self.position_mark == 0 and rocThre<0 and ref_price<lower_band:
                    sell_price = ref_price
                    sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                    order_side=TradeType.SELL, amount=Decimal(self.order_amount), price=sell_price)
                    proposalLs.append(sell_order)   
                
                #
                #离场线计算，离场均线是1某个周期均线的计算值；
                #
                #没有持仓，则为默认周期均线；
                #有持仓，则每经过1单位周期，则离周期-1来计算均线；即随着持仓递减；
                if  self.position_mark==0:
                    self.liqPeri=liqLen
                elif self.position_mark != 0: #@FIXME 这里需要根据candle周期来计算判断，否则每个tick 会-1
                    self.liqPeri=self.liqPeri-1 #这里需要先判断是否是同一个candle单位周期中不用每个tick都-1，需要判断timestamp
                    self.liqPeri=max(self.liqPeri,5)
                    
                #离场移动平均线值，这里算法与bolling的sma没有用同一个，其实应该同一个算法
                liqMeanV=candlesDf.iloc[1:self.liqPeri]['close'].mean() 
                # self.log_with_clock(logging.INFO, f"liqMeanV: {liqMeanV}")
                #有变化则放入说明candles已经变化 @FIXME 其实就应该根据candles时间中戳变化来判断情况
                if len(self.liqQue)<=0 or abs(self.liqQue[-1]-liqMeanV)>0.000001: 
                    self.liqQue.append(liqMeanV)

                #
                #离场平仓动作； 尝试平仓用MARKET市场价？限价单有可能滑单；okx只能用limit
                #持多单时，自适应出场均线低于布林通道上轨，且价格下破自适应出场均线，平多；
                if self.position_mark > 0 and liqMeanV < upper_band and  ref_price< liqMeanV: # ++ and liqMeanV>sma?
                    #@FIXME 这里卖单替代平多
                    sell_price = ref_price
                    sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                    order_side=TradeType.SELL, amount=Decimal(self.order_amount), price=sell_price)
                    proposalLs.append(sell_order)   
                # 持空单时，自适应出场均线高于布林通道下轨，且价格上破自适应出场均线，平空；
                elif self.position_mark < 0 and liqMeanV > lower_band and  ref_price> liqMeanV:
                    #@FIXME 这里买单替代平空
                    buy_price=ref_price
                    buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                       order_side=TradeType.BUY, amount=Decimal(self.order_amount), price=buy_price)
                    proposalLs.append(buy_order)
            
                
                #实际下单 这里已有订单则等待；
                if self.isOrderTracked==False and proposalLs is not None and len(proposalLs) >0:
                    self.place_orders(proposalLs)
                    self.isOrderTracked=True;
                
                # if self.isOrderTracked==False：
                #     self.log_with_clock(logging.INFO, f'已有未成交订单')
            except Exception as e:
                self.log_with_clock(logging.ERROR, f"except on on_tick:{str(e)}")
                self.logger().exception(f"except on on_tick:{e}")
                # @学习另一些打印错误栈的方法；
                # err_msg=traceback.format_exc()
                # self.logger().error((f"except on on_tick:\n{err_msg}")                     
            finally: #
                #死活 设定下次执行时间
                self.create_timestamp = self.order_refresh_time + self.current_timestamp #下次下单时间点，这里其实用的tick周期
        pass
            
    def format_status(self)->str:
        '''
        status command is issued.
        status用户执行命令时运行；
        '''
        # if not self.ready_to_trade:
        #     return "Market connectors are not ready."
        
        # re=self.getCandles();
        # self.log_with_clock(logging.INFO, str(re))
        
        lines = []
        lines.extend(["std5mQue:"]+[str(self.std5mQue.copy())])
        lines.extend(["std20mQue:"]+[str(self.std20mQue.copy())]) 
        lines.extend([" "])

        lines.extend(["bollUBQue:"]+[str(self.bollUBQue)])
        lines.extend(["bollSmaQue:"]+[str(self.bollSmaQue)]) 
        lines.extend(["bollSmaQue:"]+[str(self.bollLBQue)]) 
        lines.extend([" "])

        lines.extend(["liqQue:"]+[str(self.liqQue.copy())])
        lines.extend([" "])
        
        candlesDf=self.currentCandlesDf
        candlesDf=candlesDf.iloc[0:2]
        lines.extend(["1分钟k线信息[1m-kline]:"] + ["    " + line for line in candlesDf.to_string(index=False).split("\n")])
        lines.extend([" "])
        return "\n".join(lines)
    
        pass
        
    # 计算布林带
    def calc_bollinger_bands(self,closes, n=20, k=2):
        sma = sum(closes[-n:]) / n
        std = (sum((x - sma) ** 2 for x in closes[-n:]) / n) ** 0.5 #标准差？ 
        upper_band = sma + k * std
        lower_band = sma - k * std
        return (upper_band, sma, lower_band)
    
    
    #
    #执行后回调
    #
    def did_fill_order(self, event: OrderFilledEvent):
        '''
        完成订单交易完成后回调
        日志与提示界面通知。
        '''
        msg = (f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.exchange} at {round(event.price, 2)}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

        
        if self.position_mark==0:# 开仓，并标记 
            if event.trade_type == TradeType.BUY: self.position_mark= 1 
            elif event.trade_type == TradeType.SELL: self.position_mark= -1 
            if self.position_mark!=0: self.log_with_clock(logging.INFO, f"开仓，position_mark为：{self.position_mark}")
            else:self.log_with_clock(logging.ERROR,\
                    f"开仓后设置标志异常，没匹配到TradeType：{event.trade_type}，position_mark为：{self.position_mark}")
        else:#self.position_mark!=0 平仓，并标记
            self.position_mark= 0
            self.log_with_clock(logging.INFO, f"平仓，position_mark为：{self.position_mark}")

       
        self.isOrderTracked=False;

    
    def did_cancel_order(self, cancelled_event:OrderCancelledEvent):
        #移除策略层跟踪的对应订单；

        # ScriptStrategyBase.did_cancel_order(self, cancelled_event)
        #super().did_cancel_order(cancelled_event)
        order_id=cancelled_event.order_id
        #self.orderTracking.ignoreOrder(order_id)
        # self.isOrderTracked=False;
        pass
        
    def did_create_buy_order(self,event:BuyOrderCreatedEvent ):
        #移除策略层跟踪的对应订单；

        order_id = event.order_id #获取订单id
        #self.orderTracking.regBuyOrder(order_id)

        self.isOrderTracked=True;
        pass
    
    
    def did_create_sell_order(self,event:SellOrderCreatedEvent ):
        #移除策略层跟踪的对应订单；
        order_id = event.order_id #获取订单id
        # self.orderTracking.regSellOrder(order_id)

        self.isOrderTracked=True;
        pass
    
    #
    # 主动动作封装 action
    #       
    def cancel_all_orders(self): #暂时没用到，取消所有底层跟踪订单
        '''
        撤销所有活动订单,这里是底层框架跟踪中的订单。
        '''
        for order in self.get_active_orders(connector_name=self.exchange): #这里拿出了所有底层跟踪的订单
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)
    
    #创建可以下订的 候选订单列表
    def create_proposal(self) -> List[OrderCandidate]:
        '''
        创建订单申请List[DTO]
        关键方法 connector.get_price_by_type()
            OrderCandidate
        '''
        #获取参考价格，这里使用price_source中的类型（如：PriceType.MidPrice 买1卖1计算的中间价）
        #这里直接用 ***get_price_by_type 计算获取个特定类型价格；这个方法可以获取各种类型价格。get_price get_mid_price都是基于这个实现？
        ref_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source)
        # 根据spread 计算目标订单申请的价格。
        #buy_price = ref_price * Decimal(1 - self.bid_spread)
        #sell_price = ref_price * Decimal(1 + self.ask_spread)

        #创建买入订单申请 这里使用内置dto？：OrderCandidate
        #***主要属性：trading_pair,is_maker,order_type,order_side,amount,price
        #buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
        #                           order_side=TradeType.BUY, amount=Decimal(self.order_amount), price=buy_price)
        #创建卖出订单申请
        #sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
        #                            order_side=TradeType.SELL, amount=Decimal(self.order_amount), price=sell_price)

        #return [buy_order, sell_order] #返回买卖订单申请列表
        return []
    
    
    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        #候选订单下单
        for order in proposal:
            self.place_order(connector_name=self.exchange, order=order)
        pass
    def place_order(self, connector_name: str, order: OrderCandidate):
        '''
        根据订单b/s类型分别下单，（可以固定写法）
        *** self.sell，self.buy工具方法
        '''
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)
    

        
        