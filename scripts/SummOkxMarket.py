'''
Created on 2023年6月8日

基于bolling带进行趋势操作；
初步实现布林强盗策略。


@author: wfeng007
'''
from typing import Any, Dict, List, Set
from collections import deque
import requests
import logging
import importlib
import sys

import hummingbot.client.settings as settings
from hummingbot.strategy.order_tracker import OrderTracker
from hummingbot.logger import HummingbotLogger

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.data_type.common import PriceType, OrderType, TradeType
from hummingbot.core.data_type.limit_order import LimitOrder
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent, BuyOrderCreatedEvent, \
    SellOrderCreatedEvent, OrderCancelledEvent


# from .summ_hbot_okx_utilz import getCandlesDf
import scripts.summ_hbot_okx_utilz as summUtilz 

from decimal import Decimal
import pandas as pd



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
        
    
    #布林带信息：
    bollUBQue=deque([], maxlen=10) #上带
    bollSmaQue= deque([], maxlen=10)
    bollLBQue= deque([], maxlen=10)#下带
    
    currentCandlesDf=None
    
    #刷新周期
    order_refresh_time = 11
    #本实例中的 创建tick时间戳
    create_timestamp = 0

    #趋势策略，持仓标志,<0空头仓 >0多头仓 0无仓位 #TODO 这里因为现货用简单仓位标志来记录仓位；
    position_mark=0
    liqQue=deque([], maxlen=10)
    # isOrderTracked=False
    
    
    def __init__(self,connectors: Dict[str, ConnectorBase]):
        '''
        初始化
        '''
        super().__init__(connectors)
            #初始化策略层订单跟踪器
        self.orderTracking=StrategyOrderTracking(strategyMaster=self,
                                                 exchange=self.exchange,
                                                 trading_pair=self.trading_pair,
                                                 base_order_tracker=self.order_tracker)
        summUtilz=reload_module("summ_hbot_okx_utilz")#重新加载

    def on_tick(self):
        
        # 获取info信息
        # for connectorName,connector in self.connectors.items():
        #     self.logger().info(f"Connector:{connectorName}")
        #     self.logger().info(f"Best ask:{connector.get_price('ETH-USDT',True)}") #卖
        #     self.logger().info(f"Best bid:{connector.get_price('ETH-USDT',False)}") #买
        #     self.logger().info(f"Mid price:{connector.get_mid_price('ETH-USDT')}") #中价
        if self.create_timestamp <= self.current_timestamp:
            try:
                candlesDf=summUtilz.getCandlesDf(pairName=f"{self.BASE_ASSET}-{self.QUOTE_ASSET}", period="1m",limit=21)
                self.currentCandlesDf=candlesDf 
                
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
                    buy_price= None  #下单时使用bestprice
                    buy_order = self._createOrderCandi(order_side=TradeType.BUY,price=buy_price) 
                    proposalLs.append(buy_order)
                
                #无空头仓，且roc过滤器为负，突破下线，开空仓；
                elif self.position_mark == 0 and rocThre<0 and ref_price<lower_band:
                    sell_price = None  #下单时使用bestprice
                    sell_order =self._createOrderCandi(order_side=TradeType.SELL,price=sell_price) 
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
                    # 这里卖单替代平多
                    sell_price = None #下单时使用bestprice
                    sell_order = self._createOrderCandi(order_side=TradeType.SELL,price=sell_price) 
                    proposalLs.append(sell_order)   
                # 持空单时，自适应出场均线高于布林通道下轨，且价格上破自适应出场均线，平空；
                elif self.position_mark < 0 and liqMeanV > lower_band and  ref_price> liqMeanV:
                    # 这里买单替代平空
                    buy_price=None #下单时使用bestprice
                    buy_order = self._createOrderCandi(order_side=TradeType.BUY,price=buy_price) 
                    proposalLs.append(buy_order)
            
                
                #实际下单 这里已有订单则；
                if not self.orderTracking.isOrderTracked and proposalLs is not None and len(proposalLs) >0:
                    self.place_orders(proposalLs,isUsingBestPrice=True) #在下单时使用最容易成交的价格
                    # self.isOrderTracked=True;
                
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
    
    def _createOrderCandi(self,order_side:TradeType,price:Decimal=None):
        return OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,\
                order_side=order_side, amount=Decimal(self.order_amount), price=price)
    
    def format_status(self)->str:
        '''
        展示BB数据，以及布林强盗算法中的离场移动均线；
        '''
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        # warning_lines = []
        # warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))
        
        lines = []
        lines.extend([f"策略基本信息：{self.STRATEGY_INFO} "])
        lines.extend([" "])
        lines.extend([f"是否有跟踪订单isOrderTracked:{self.orderTracking.isOrderTracked}"])
        try:
            trackedDf = self.orderTracking.getTrackedOrdersDf()
            lines.extend(["策略层跟踪订单StrategyTrackedOrders:"] \
                         + ["    " + line for line in trackedDf.to_string(index=False).split("\n")])
        except ValueError:
            lines.extend(["没有策略层订单 No Strategy Orders."])
        lines.extend([" "])
        lines.extend(["仓位情况标志,position_mark："+str(self.position_mark)])
        lines.extend([" "])

        lines.extend(["BB&Liq布林与离场线:"]+["bollUBQue:"+str(self.bollUBQue)])
        lines.extend(["bollSmaQue:"+str(self.bollSmaQue)]) 
        lines.extend(["bollLBQue:"+str(self.bollLBQue)]) 
        lines.extend(["liqQue:"+str(self.liqQue)])
        lines.extend([" "])
        
        candlesDf=self.currentCandlesDf
        candlesDf=candlesDf.iloc[0:3]
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

        self.orderTracking.ignoreOrder(event.order_id) 

    
    def did_cancel_order(self, cancelled_event:OrderCancelledEvent):
        #移除策略层跟踪的对应订单；

        # super().did_cancel_order(cancelled_event) #顶层没做啥
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
        # ref_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source)
        return []
    
    
    def place_orders(self, proposal: List[OrderCandidate],isUsingBestPrice:bool=False) -> None:
        #候选订单下单
        for order in proposal:
            if isUsingBestPrice and order.price is None :
                plcOdrPrice=self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestAsk)\
                    if order.order_side == TradeType.BUY else self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestBid)\
                    if order.order_side == TradeType.SELL else self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.MidPrice) 
                order.price=plcOdrPrice

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
    



class StrategyOrderTracking:
    '''
    策略层订单跟踪器；
    '''
    
    #hbot后台系统日志器
    _logger=None
    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger
    
    '''
    策略层跟踪订单模块
    '''
    def __init__(self,strategyMaster:ScriptStrategyBase,exchange,trading_pair,base_order_tracker=None):
        self.strategyMaster=strategyMaster
        self.exchange=exchange
        self.trading_pair=trading_pair
        #init 
        self.tracked_buy_order_id=None
        self.tracked_sell_order_id=None
        self.base_order_traker:OrderTracker=base_order_tracker; #框架基础层订单跟踪器
        if self.base_order_traker is None: self.base_order_traker=self.strategyMaster.order_tracker
    
    def regBuyOrder(self,orderId):
        self.tracked_buy_order_id=orderId
        pass
    
    def regSellOrder(self,orderId):
        self.tracked_sell_order_id=orderId
        pass
    
    def cancelCurrentOrders(self):
        '''
        撤销上层跟踪中的订单,使用内置master发送撤销信号。
        '''
        #
        #只取消上层跟踪订单
        #
        if self.tracked_buy_order_id is not None:      
            toCid=self.tracked_buy_order_id
            self.strategyMaster.cancel(self.exchange, self.trading_pair, toCid)
            self.tracked_buy_order_id=None
            
        if self.tracked_sell_order_id is not None:
            toCid=self.tracked_sell_order_id
            self.strategyMaster.cancel(self.exchange, self.trading_pair, toCid)
            self.tracked_sell_order_id=None
        pass
    
    # def _get_active_orders(self, connector_name: str) -> List[LimitOrder]:
    #     """
    #     Returns a list of active orders for a connector.
    #     :param connector_name: The name of the connector.
    #     :return: A list of active orders
    #     """
    #     orders = self.base_order_traker.active_limit_orders
    #     #@todo 如果只是获取order可不可以不用这个connector 
    #     #看内部 o[0] 就是MarketTradingPairTuple的market_pair.market,这个值确实是connector
    #     #跟踪看结构为：MarketTradingPairTuple(self.connectors[connector_name], trading_pair, base, quote)
    #     #    market: ExchangeBase
    #     #    trading_pair: str
    #     #    base_asset: str
    #     #    quote_asset: str
    #     connector = self.strategyMaster.connectors[connector_name] 
    #     return [o[1] for o in orders if o[0] == connector]
    
    # def getBaseTrackerOrdersDf(self) -> pd.DataFrame:
    #     """
    #     返回框架基础层跟踪的订单，主要为了展示。
    #     Return a data frame of all base tracked active orders for displaying purpose.
    #     """
    #     columns = ["Exchange", "Market", "Side", "Price", "Amount", "Age","Order ID"]
    #     data = []
    #     for order in self._get_active_orders(self.exchange):
    #         age_txt = "n/a" if order.age() <= 0. else pd.Timestamp(order.age(), unit='s').strftime('%H:%M:%S')
    #         data.append([
    #             self.exchange,
    #             order.trading_pair,
    #             "buy" if order.is_buy else "sell",
    #             float(order.price),
    #             float(order.quantity),
    #             age_txt,
    #             order.client_order_id if len(order.client_order_id) <=10 else ("..."+order.client_order_id[-7:]) #自己增加,限长id
    #         ])
            
    #     if not data:
    #         raise ValueError
    #     df = pd.DataFrame(data=data, columns=columns)
    #     df.sort_values(by=["Exchange", "Market", "Price"],ascending=False, inplace=True) #
    #     return df
    
    def _getOrderByIdFromBaseTracker(self,orderId):
        if orderId is None:
            return None;
        # self.logger().info(f"self.trading_pair:{self.trading_pair} oid:{orderId}")
        mpair=self.base_order_traker.get_market_pair_from_order_id(orderId)
        if mpair is None:
            self.logger().info(f"market_pair:{mpair}")
            return None;
        #trading_pair:MarketTradingPairTuple get_limit_order需要MarketTradingPairTuple类型 （market_pair_tpl）
        ctOrder=self.base_order_traker.get_limit_order(mpair,orderId) #get_limit_order(self, market_pair, order_id: str) -> LimitOrder:
        # self.logger().info(f"ctOrder:{ctOrder}")
        return ctOrder;
    
        
    def getTrackedOrdersDf(self):
        '''
        参考hbot，封装出当前本策略层跟踪订单的Dataframe结构。
        '''
        ctBuyOid=self.tracked_buy_order_id
        ctSellOid=self.tracked_sell_order_id
        
        columns = ["Exchange", "Market", "Side", "Price", "Amount", "Age","Order ID"]
        orderLs=[]
        ctBuyOrder=self._getOrderByIdFromBaseTracker(ctBuyOid)
        if ctBuyOrder is not None: orderLs.append(ctBuyOrder)
        ctSellOrder=self._getOrderByIdFromBaseTracker(ctSellOid)
        if ctSellOrder is not None: orderLs.append(ctSellOrder)
        
        data = []
        for order in orderLs:
                age_txt = "n/a" if order.age() <= 0. else pd.Timestamp(order.age(), unit='s').strftime('%H:%M:%S')
                data.append([
                    self.exchange,
                    order.trading_pair,#
                    "buy" if order.is_buy else "sell",
                    float(order.price),
                    float(order.quantity),
                    age_txt,
                    order.client_order_id if len(order.client_order_id) <=10 else ("..."+order.client_order_id[-7:]) #自己增加,限长id
                ])
                
        if not data: #没有数据应该报错？
            raise ValueError
        df = pd.DataFrame(data=data, columns=columns)
        # df.sort_values(by=["Exchange", "Market", "Side"], inplace=True) #2条不用排序
        return df
    
    def ignoreOrder(self,orderId): #did cancel的时候调用
        '''
        取消指定订单订单跟踪
        '''
        if self.tracked_buy_order_id is not None and self.tracked_buy_order_id == orderId :self.tracked_buy_order_id=None
        if self.tracked_sell_order_id is not None and self.tracked_sell_order_id == orderId:self.tracked_sell_order_id=None
        # self.tracked_buy_order_id=None if self.tracked_buy_order_id == orderId else self.tracked_buy_order_id #fixme 不是应该无需赋值
        # self.tracked_sell_order_id=None if self.tracked_sell_order_id == orderId else self.tracked_sell_order_id #fixme 不是应该无需赋值
        pass
    
    def ignoreCurrentAll(self): #为了hanging
        self.tracked_buy_order_id=None
        self.tracked_sell_order_id=None
        pass

    @property
    def isOrderTracked(self):
        return self.tracked_buy_order_id is not None or self.tracked_sell_order_id is not None

    pass        
        
def reload_module(moduleFileName):
        """
        重新动态加载依赖的module （脚本更新，依赖的模块也可能更新了。注意依赖的模块尽量不要有状态。）
        Imports the script module based on its name (module file name) 
        """
        module_name = moduleFileName
        module = sys.modules.get(f"{settings.SCRIPT_STRATEGIES_MODULE}.{module_name}")
        if module is not None:
            module = importlib.reload(module)
        else:
            module = importlib.import_module(f".{module_name}", package=settings.SCRIPT_STRATEGIES_MODULE)

        return module