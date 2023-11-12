'''
Created on 2023年6月8日

基于bolling带进行趋势操作；
初步实现布林强盗策略。


@author: wfeng007
'''
from typing import Any, Dict, List, Set
from collections import deque
# import requests
import logging
import importlib
import sys
import abc

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
from . import summ_hbot_script_utilz as summ_scri_util
from . import summ_hbot_okx_utilz as summ_okx_util #summUtilz 

from decimal import Decimal
import pandas 

class Context:
    #market：
    refPrice:Decimal #refPrice 当前参考价
    proposalLs:List[OrderCandidate] #proposalLs 用来向市场下单；

    #candles:
    #nowCandlesDf
    #nowCandleTimestamp
    #isNewCandlePeriod,isCandleBarOpen
    #

    #strategy
    #
    pass

# summ_okx_util=summ_scri_util.reload_module("summ_hbot_okx_utilz")#重新加载，热加载
class SummOkxMarketAbs(ScriptStrategyBase,metaclass=abc.ABCMeta):
    '''
    获取市场信息形成指标线；信息使用candle K线蜡烛图；
    基于指标线进行交易操作；
    '''
    # exchange="okx"
    BASE_ASSET="ETH"
    QUOTE_ASSET="USDT"
    EXCHANGE_NAME="okx" #OK交易所
    STRATEGY_INFO=f"SummOkxMarket v20231029"

    #
    # 市场、交易对信息；hbot用的类属性操作所以只能放init()外面；
    #
    trading_pair = f"{BASE_ASSET}-{QUOTE_ASSET}"
    # exchange = "binance_paper_trade"
    exchange = EXCHANGE_NAME
    #框架逻辑会基于markets进行connector初始化等；
    markets={
        exchange: {trading_pair} #okx是正式的exchange
    }
        
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
        

        #框架属性初始化：
        #todo事情的刷新周期
        self.todo_refresh_time = 7 #11
        #本实例中的 创建tick时间戳
        self.next_tick_timestamp = 0
        #并发锁
        self.paraLocked:bool=False #理论上不会并行，以防万一；

        # market取价类型等
        # price_source = PriceType.MidPrice
        

        # candle处理相关属性 @TODO 其实这里可以直接记录上次tick整个candlebar
        self.currentCandlesDf=None
        self.lastCandleTimestamp=None
        # self.lastTickCandleBar=None  @TODO 其实这里可以直接记录上次tick整个candlebar
        
        #策略初始化
        #量
        self.strategyInitialize()

   
    def on_tick(self):
        # tick进入执行点，且未加锁（并发锁）状态则执行本次tick 
        if self.next_tick_timestamp <= self.current_timestamp and not self.paraLocked: #如果有还在运行则直接结束
            context=Context()
            try:
                self.paraLocked=True #加锁
                ####
                # 主流程：
                ####

                #初始化与解析市场信息；
                self._parseMarket(context)
                #
                # 当前candles获取
                #
                self._getAndParseCandlesDf(context)

                #
                # 策略主干实现：继承本类代码实现3个方法 
                # 
                # 策略-量化分析
                #
                self.strategyDataParse(context)
                #
                # 策略-入场信号识别与计算下单
                #
                self.strategyEntrySignalAndProposeOrder(context)
                #
                # 策略-离场信号识别与计算下单
                #
                self.strategyExitSignalAndProposeOrder(context)
                #
                
                # 策略-实际下单：
                self.strategyPlaceOrder(context)


            except Exception as e:
                self.log_with_clock(logging.ERROR, f"except on on_tick:{str(e)}")
                self.logger().exception(f"except on on_tick:{e}")
                # @学习另一些打印错误栈的方法；
                # err_msg=traceback.format_exc()
                # self.logger().error((f"except on on_tick:\n{err_msg}")                     
            finally: #
                
                #candle框架close
                self._updateStateAndCloseCandles(context)

                #死活 设定下次执行时间
                self.next_tick_timestamp = self.todo_refresh_time + self.current_timestamp #下次下单时间点，这里其实用的tick周期
                #释放锁
                self.paraLocked=False
                
        elif self.next_tick_timestamp <= self.current_timestamp and self.paraLocked:
            self.log_with_clock(logging.INFO, f"tick-Thread locked,self.paraLocked:{self.paraLocked}!")

    @abc.abstractmethod
    def strategyInitialize(self):
        ...

    def _getAndParseCandlesDf(self,context:Context)->pandas.DataFrame:
        #获取当前K图信息;
        candlesDf:pandas.DataFrame=summ_okx_util.getCandlesDf(pairName=f"{self.BASE_ASSET}-{self.QUOTE_ASSET}", period="1m",limit=21)
        self.currentCandlesDf=candlesDf.copy() #修改不会影响 self.currentCandlesDf

        # context 范围处理
        context.nowCandlesDf=candlesDf
        nowCdlTs=candlesDf.iloc[-1]['timestamp']#使用candledf中的类型

        context.nowCandleTimestamp=nowCdlTs
        context.isNewCandlePeriod=False
        #根据是否candle新周期执行,；
        if nowCdlTs != self.lastCandleTimestamp :
            context.isNewCandlePeriod:bool=True
            
        context.isCandleBarOpen:bool=context.isNewCandlePeriod #别名CandleBarOpen
        
        return candlesDf

    def _parseMarket(self,context:Context):
        ref_price = self.connectors[self.exchange]\
                .get_price_by_type(self.trading_pair, PriceType.MidPrice) #注意ref_price 这里得到是Decimal 类型
        context.refPrice=ref_price
        #初始化下单列表
        proposalLs:List[OrderCandidate] = []
        context.proposalLs=proposalLs

    @abc.abstractmethod
    def strategyDataParse(self,context:Context):
        ...

    @abc.abstractmethod
    def strategyEntrySignalAndProposeOrder(self,context:Context):
        ...

    @abc.abstractmethod
    def strategyExitSignalAndProposeOrder(self,context:Context):
        ...

    def strategyPlaceOrder(self,context:Context):
        #实际下单 这里已有订单则；
        self.orderTracking.clearNoBaseTracking() #清理其实已经非跟踪订单
        if not self.orderTracking.isOrderTracked and context.proposalLs is not None and len(context.proposalLs) >0:
            self._place_orders(context.proposalLs,isUsingBestPrice=True) #在下单时使用最容易成交的价格
        pass

    def _updateStateAndCloseCandles(self,context:Context):
        self.lastCandleTimestamp=context.nowCandleTimestamp #parse 后lastCandle已经更新；
        pass

    def _createOrderCandi(self,order_side:TradeType,price:Decimal=None):
        return OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,\
                order_side=order_side, amount=Decimal(self.order_amount), price=price)

    def _place_orders(self, proposal: List[OrderCandidate],isUsingBestPrice:bool=False) -> None:
        #候选订单下单
        for order in proposal:
            if isUsingBestPrice and order.price is None :
                plcOdrPrice=self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestAsk)\
                    if order.order_side == TradeType.BUY else self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestBid)\
                    if order.order_side == TradeType.SELL else self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.MidPrice) 
                order.price=plcOdrPrice

            self._place_order(connector_name=self.exchange, order=order)
        pass


    #
    # 主动动作封装 action
    #  
    def _place_order(self, connector_name: str, order: OrderCandidate):
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

    
     
    def cancel_all_orders(self): #暂时没用到，取消所有底层跟踪订单
        '''
        撤销所有活动订单,这里是底层框架跟踪中的订单。
        '''
        for order in self.get_active_orders(connector_name=self.exchange): #这里拿出了所有底层跟踪的订单
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)
    
    #创建可以下订的 候选订单列表
    # def create_proposal(self) -> List[OrderCandidate]:
    #     '''
    #     创建订单申请List[DTO]
    #     关键方法 connector.get_price_by_type()
    #         OrderCandidate
    #     '''
    #     return []
    


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

    def clearNoBaseTracking(self): #但是每次都清理其实也比较消耗性能@TODO 封装1个直接返回mpair即可？
        #由于回调的异步交错执行或其他原因，导致跟踪的orderId是脏数据；需要定期或手动清理。尤其底层跟踪器已经维护时；进行完成清理；
        if self.tracked_buy_order_id is not None and self._getOrderByIdFromBaseTracker(self.tracked_buy_order_id) is None:
            self.logger().info(f"Tracking.clearNoBaseTracking() tracked_buy_order_id will be clear:{self.tracked_buy_order_id}")
            self.tracked_buy_order_id=None
        if self.tracked_sell_order_id is not None and self._getOrderByIdFromBaseTracker(self.tracked_sell_order_id) is None:
            self.logger().info(f"Tracking.clearNoBaseTracking() tracked_sell_order_id will be clear:{self.tracked_sell_order_id}")
            self.tracked_sell_order_id=None 

    
    def _getOrderByIdFromBaseTracker(self,orderId):
        if orderId is None:
            return None
        # self.logger().info(f"self.trading_pair:{self.trading_pair} oid:{orderId}")
        mpair=self.base_order_traker.get_market_pair_from_order_id(orderId)
        if mpair is None:
            self.logger().info(f"Tracking._getOrderByIdFromBaseTracker() get market_pair is:{mpair} with orderId:{orderId}"\
                               +",will return None.")
            return None
        #trading_pair:MarketTradingPairTuple get_limit_order需要MarketTradingPairTuple类型 （market_pair_tpl）
        ctOrder=self.base_order_traker.get_limit_order(mpair,orderId) #get_limit_order(self, market_pair, order_id: str) -> LimitOrder:
        # self.logger().info(f"ctOrder:{ctOrder}")
        return ctOrder
    
        
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
                age_txt = "n/a" if order.age() <= 0. else pandas.Timestamp(order.age(), unit='s').strftime('%H:%M:%S')
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
        df = pandas.DataFrame(data=data, columns=columns)
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
        