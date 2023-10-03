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
                if len(self.bollUBQue)<=0 or abs(self.bollUBQue[-1]-upper_band)>0.000001:
                    self.bollUBQue.append(upper_band)
                if len(self.bollSmaQue)<=0 or abs(self.bollSmaQue[-1]-sma)>0.000001:
                    self.bollSmaQue.append(sma)
                if len(self.bollLBQue)<=0 or abs(self.bollLBQue[-1]-lower_band)>0.000001:
                    self.bollLBQue.append(lower_band)
                
                # 需要扩展的部分；
                #
                #@TODO交易逻辑
                #
                #2 完成情况1，买入操作: 
                #lb   < 5ma < 20ma/mb < 现价 < ub
                #lb   < 20ma/mb < 5ma < 现价 < ub
                #
                #1 实用简单版：过于简单。
                # 现价>ub 卖出；
                # 现价<lb 买入； 
                #
                proposalLs:List[OrderCandidate] = []
                ref_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source)
                if ref_price>upper_band:
                    sell_price=ref_price
                    sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                    order_side=TradeType.SELL, amount=Decimal(self.order_amount), price=sell_price)
                    proposalLs.append(sell_order)
                elif ref_price<lower_band:
                    buy_price=ref_price
                    buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                       order_side=TradeType.BUY, amount=Decimal(self.order_amount), price=buy_price)
                    proposalLs.append(buy_order)
                else:
                    pass
                    
                if proposalLs is not None and len(proposalLs) >0:
                    self.place_orders(proposalLs)
                
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
        lines.extend(["std5mQue:"]+[str(self.std5mQue)])
        lines.extend(["std20mQue:"]+[str(self.std20mQue.copy())]) 
        lines.extend([" "])
        
        lines.extend(["bollUBQue:"]+[str(self.bollUBQue)])
        lines.extend(["bollSmaQue:"]+[str(self.bollSmaQue)]) 
        lines.extend(["bollSmaQue:"]+[str(self.bollLBQue)]) 
        
        lines.extend([" "])
        
        candlesDf=self.currentCandlesDf
        candlesDf=candlesDf.iloc[0:6]
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
        
    
    def did_cancel_order(self, cancelled_event:OrderCancelledEvent):
        #移除策略层跟踪的对应订单；

        # ScriptStrategyBase.did_cancel_order(self, cancelled_event)
        # super().did_cancel_order(cancelled_event)
        order_id=cancelled_event.order_id
        self.orderTracking.ignoreOrder(order_id)
        pass
        
    def did_create_buy_order(self,event:BuyOrderCreatedEvent ):
        #移除策略层跟踪的对应订单；

        order_id = event.order_id #获取订单id
        self.orderTracking.regBuyOrder(order_id)
        pass
    
    
    def did_create_sell_order(self,event:SellOrderCreatedEvent ):
        #移除策略层跟踪的对应订单；
        order_id = event.order_id #获取订单id
        self.orderTracking.regSellOrder(order_id)
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
    

        
        