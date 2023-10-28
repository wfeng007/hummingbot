'''
Created on 2022年11月27日

做市交易策略-v1

@author: wfeng007
'''
from decimal import Decimal
import logging
from typing import Dict, List

from hummingbot.strategy.order_tracker import OrderTracker
from hummingbot.logger import HummingbotLogger

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import PriceType, OrderType, TradeType
from hummingbot.core.data_type.limit_order import LimitOrder
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent, BuyOrderCreatedEvent, \
    SellOrderCreatedEvent, OrderCancelledEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase

import pandas as pd

from .summ_hbot_script_utilz import PriInvTransBySqr

# lsb_logger = None

class SummPMM(ScriptStrategyBase):
    '''
    自制存做市商交易策略。简单注释。
    '''
    
    BASE_ASSET = "ETH"
    QUOTE_ASSET = "USDT"
    EXCHANGE_NAME="okx" #OK交易所
    
    
    #上下幅度
    bid_spread = 0.30 * 0.01 #pct
    ask_spread = 0.30 * 0.01 #pct
    extend_spread =0.30 * 0.01#pct
    
    #量
    order_amount = 0.02
    #刷新周期
    order_refresh_time = 600*6*2 #s
    # PriceType 中也有lastTrade（最后的交易价）
    price_source = PriceType.MidPrice
    
    #
    trading_pair = f"{BASE_ASSET}-{QUOTE_ASSET}"
    # exchange = "binance_paper_trade"
    exchange = EXCHANGE_NAME
    
    #标的交易对信息 其实这个是类属性
    #内部有的函数参数中connector_name就是exchange
    markets = {exchange: {trading_pair}}
    
    
    #本实例中的 创建tick时间戳
    create_timestamp = 0 #这个每次tick会更新？
    
    STRATEGY_INFO=f"SummPMM v20230122"

    =

    def __init__(self,connectors: Dict[str, ConnectorBase]):
        '''
        初始化
        '''
        super().__init__(connectors)
        #初始化库存信息缓存
        self.currentInventory=None
        #初始化策略层订单跟踪器
        self.orderTracking=StrategyOrderTracking(strategyMaster=self,
                                                 exchange=self.exchange,
                                                 trading_pair=self.trading_pair,
                                                 base_order_tracker=self.order_tracker)
        
        self.isInvAllocatedExcessive=False

    def on_tick(self):
        '''
        tick周期回调
        简单的根据时间周期挂做商单；最简化的基本运作流程。
        *** self.current_timestamp TimeIterator类的属性用来实现tick动作。
        '''
        
        #
        #库存信息计算 基于connector.get_balance(asset)等
        self.currentInventory=InventoryInfo(strategyMaster=self,refPrice=self.getMidPrice(),extendSpread=self.extend_spread) # 
        
        #每次都计算，其实可以根据一定周期计算
        # self.ask_spread=self.currentInventory.newAskSpread()
        # self.bid_spread=self.currentInventory.newBidSpread()
        
        #定期订单刷新 下单
        if self.create_timestamp <= self.current_timestamp: #需要刷新订单
            try:
                
                #下单前先更新对象级spread
                self.ask_spread=self.currentInventory.newAskSpread()
                self.bid_spread=self.currentInventory.newBidSpread()
                #
                #判断是否可以下单 
                #1判断库存是否分配过度，进入冷却 @todo 看是否要封装到库存的统一逻辑一起；策略类或库存类都行
                if  not self.isInvAllocatedExcessive and \
                    (self.currentInventory.baseAvailableRate is not None and self.currentInventory.baseAvailableRate<0.2) or \
                    self.currentInventory.quoteAvailableRate<0.2:
                    self.isInvAllocatedExcessive=True
                    self.log_with_clock(logging.INFO, f"库存分配过度！")
                    return #当前简单情况配合finally可用，否则考虑更改流程逻辑
                #恢复
                if  self.isInvAllocatedExcessive and \
                    (self.currentInventory.baseAvailableRate is not None and self.currentInventory.baseAvailableRate>0.5) and \
                    self.currentInventory.quoteAvailableRate>0.5:
                    self.isInvAllocatedExcessive=False
                    self.log_with_clock(logging.INFO, f"库存分配过度情况恢复！")
                
                #
                if not self.isInvAllocatedExcessive:
                    # self.cancel_all_orders() #取消跟踪中的订单
                    self.orderTracking.cancelCurrentOrders() #这里只取消策略层跟踪的订单
                    proposal: List[OrderCandidate] = self.create_proposal() #创建订单
                    proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal) #检查资产余额是否ok并做调整?
                    self.place_orders(proposal_adjusted) #下单
                else:
                    self.log_with_clock(logging.INFO, f"库存分配过度，等等情况。不进行订单动作! Inventory is Allocated Excessive .etc..No order action is taken!")
                    
            
            finally: #
                #死活 设定下次执行时间
                self.create_timestamp = self.order_refresh_time + self.current_timestamp #下次下单时间点，这里其实用的tick周期
            
        
        


    def did_fill_order(self, event: OrderFilledEvent):
        '''
        完成订单交易完成后回调
        日志与提示界面通知。
        这里增加了，本层跟踪忽略，等待对手订单被执行。
        '''
        msg = (f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.exchange} at {round(event.price, 2)}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
        
        # 取消成组合的对手订单；可能不能在fill时取消基层跟踪。而是在refresh时cancel策略层跟踪的订单，而不是所有订单。
        # 一旦有成交，当前上层跟踪up track全部清空,即忽略策略层的未成交订单。
        self.orderTracking.ignoreCurrentAll() 
        
        # @studying
        # 移除叠层跟踪订单有个
        # self.order_tracker 获取当前OrderTracker跟踪器
        # OrderTracker.stop_tracking_limit_order()
        # 需要先获得下单后的order_id
        #
        # 内部需要pair_tuple
        # market_pair_tpl=self._market_trading_pair_tuple(connector_name=self.exchange, trading_pair=self.trading_pair)
        # 脱离基层跟踪
        # self.order_tracker.stop_tracking_limit_order(market_pair=market_pair_tpl,order_id="");
        
    #todo
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

    def format_status(self)->str:
        '''
        扩展status输出，将market_status_data_frame返回的市场概要也简单显示出来。 更复杂的参考SummInfoEx.py
        *** market_status_data_frame() #返回最基本基本的买卖信息，中间价之类。
            df结构：["Exchange", "Market", "Best Bid Price", "Best Ask Price", "Mid Price"] 
        '''
        # superLine=super().format_status()
        
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        warning_lines = []
        warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))
        
        #输出Line
        lines = []
        lines.extend([f"策略基本信息：{SummPMM.STRATEGY_INFO} "])
        #当前策略对应库存基本信息base,quote asset balance
        
        lines.extend([f"库存信息[InventoryInfo]:"])
        currentInventory = self.currentInventory
        if currentInventory is not None: 
            invDf=currentInventory.getDataframe()
            lines.extend(["    " + line for line in invDf.to_string(index=False).split("\n")])
            # lines.extend([f"标的资产价值BaseValue:{currentInventory.baseInventoryValue} "]+
            #          [f"计价资产价值QuoteValue:{currentInventory.quoteInventoryValue} "]+
            #          [f"总体资产价值TotalValue:{currentInventory.totalInventoryValue} "]+
            #          [f"标的资产比例BasePct(.2%):{currentInventory.baseRate:.2%} "]+
            #          [f"计价资产比例QuotePct(.2%):{currentInventory.quoteRate:.2%} "])
        # lines.extend([" "])
        #todo 需要格式化；
        lines.extend([f"卖单价差ask_spread(.6%):{self.ask_spread:.6%} "]+
                     [f"买单价差bid_spread(.6%):{self.bid_spread:.6%} "])
        lines.extend([" "])
         
        #市场深度 基于pd datafame，表格方式呈现； 这里使用所有的markets中的交易对。
        market_status_df = self.market_status_data_frame(self.get_market_trading_pair_tuples())
        lines.extend(["市场状态概要[MarketStatus]:"] + ["    " + line for line in market_status_df.to_string(index=False).split("\n")])
        lines.extend([" "])
        
        #展示上层跟踪信息
        # lines.extend([" 上层跟踪订单id:"]\
        #             + [f"stragy_tracked_buy:{self.orderTracking.tracked_buy_order_id}"]\
        #             + [f"stragy_tracked_sell:{self.orderTracking.tracked_sell_order_id}"]\
        #              )
        try:
            trackedDf = self.orderTracking.getTrackedOrdersDf()
            lines.extend(["策略层跟踪订单StrategyTrackedOrders:"] + ["    " + line for line in trackedDf.to_string(index=False).split("\n")])
        except ValueError:
            lines.extend(["没有策略层订单 No Strategy Orders."])
        lines.extend([" "])
        
        #框架层订单
        try:
            df = self.orderTracking.getBaseTrackerOrdersDf()
            #
            # 显示框架基础层订单统计信息，及少量(6条)订单
            #
            ctSeri=df['Side'].value_counts()
            lines.extend([f"框架基础层订单Orders,买单计数buy-cou[{ctSeri['buy'] if df.shape[0]>0 and 'buy' in ctSeri else 0 }],"
                         + f"卖单计数sell-cou:[{ctSeri['sell'] if df.shape[0]>0 and 'sell' in ctSeri else 0}]:"] 
                         + ["    " + line for line in df.to_string(index=False,max_rows=6).split("\n")]
                         )
        except ValueError:
            lines.extend(["  No active maker orders."])


        warning_lines.extend(self.balance_warning(self.get_market_trading_pair_tuples()))
        if len(warning_lines) > 0:
            lines.extend(["", "*** WARNINGS ***"] + warning_lines)
        #先显：示市场状态概要
        # return "\n".join(lines)+superLine
        return "\n".join(lines)




    def getMidPrice(self):
        ref_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source) #ExchangeBase.get_price_by_type
        return ref_price
    
    #
    #
    #       
    def cancel_all_orders(self): #暂时没用到，取消所有底层跟踪订单
        '''
        撤销所有活动订单,这里是底层框架跟踪中的订单。
        '''
        for order in self.get_active_orders(connector_name=self.exchange): #这里拿出了所有底层跟踪的订单
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)
            
            
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
        buy_price = ref_price * Decimal(1 - self.bid_spread)
        sell_price = ref_price * Decimal(1 + self.ask_spread)

        #创建买入订单申请 这里使用内置dto？：OrderCandidate
        #***主要属性：trading_pair,is_maker,order_type,order_side,amount,price
        buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                   order_side=TradeType.BUY, amount=Decimal(self.order_amount), price=buy_price)
        #创建卖出订单申请
        sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                    order_side=TradeType.SELL, amount=Decimal(self.order_amount), price=sell_price)

        return [buy_order, sell_order] #返回买卖订单申请列表


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

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        '''
        调整预算,根据balance 账户越，来检测或调整订单申请。
        connector.budget_checker 默认预算检测器
        connector.budget_checker.adjust_candidates all_or_none要吗amount完整可下单，要么为0即无法下单
        '''
        #
        proposal_adjusted = self.connectors[self.exchange].budget_checker.adjust_candidates(proposal, all_or_none=True)
        return proposal_adjusted



class StrategyOrderTracking:
    '''
    策略层订单跟踪器；一般用于跟踪当前买卖对；
    关联基础底层订单跟踪器；见：getBaseTrackerOrdersDf()方法；
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
    def __init__(self,strategyMaster:ScriptStrategyBase,exchange,trading_pair,base_order_tracker):
        self.strategyMaster=strategyMaster
        self.exchange=exchange
        self.trading_pair=trading_pair
        #init 
        self.tracked_buy_order_id=None
        self.tracked_sell_order_id=None
        self.base_order_traker:OrderTracker=base_order_tracker; #框架基础层订单跟踪器
    
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
    
    def _get_active_orders(self, connector_name: str) -> List[LimitOrder]:
        """
        Returns a list of active orders for a connector.
        :param connector_name: The name of the connector.
        :return: A list of active orders
        """
        orders = self.base_order_traker.active_limit_orders
        #@todo 如果只是获取order可不可以不用这个connector 
        #看内部 o[0] 就是MarketTradingPairTuple的market_pair.market,这个值确实是connector
        #跟踪看结构为：MarketTradingPairTuple(self.connectors[connector_name], trading_pair, base, quote)
        #    market: ExchangeBase
        #    trading_pair: str
        #    base_asset: str
        #    quote_asset: str
        connector = self.strategyMaster.connectors[connector_name] 
        return [o[1] for o in orders if o[0] == connector]
    
    def getBaseTrackerOrdersDf(self) -> pd.DataFrame:
        """
        返回框架基础层跟踪的订单，主要为了展示。
        Return a data frame of all base tracked active orders for displaying purpose.
        """
        columns = ["Exchange", "Market", "Side", "Price", "Amount", "Age","Order ID"]
        data = []
        for order in self._get_active_orders(self.exchange):
            age_txt = "n/a" if order.age() <= 0. else pd.Timestamp(order.age(), unit='s').strftime('%H:%M:%S')
            data.append([
                self.exchange,
                order.trading_pair,
                "buy" if order.is_buy else "sell",
                float(order.price),
                float(order.quantity),
                age_txt,
                order.client_order_id if len(order.client_order_id) <=10 else ("..."+order.client_order_id[-7:]) #自己增加,限长id
            ])
            
        if not data:
            raise ValueError
        df = pd.DataFrame(data=data, columns=columns)
        df.sort_values(by=["Exchange", "Market", "Price"],ascending=False, inplace=True) #
        return df
    
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
    
    pass

class InventoryInfo:
    '''
    库存管理模块，当前当做VO在用
    基本基于balance数据形成
    '''
    
    # 扩展spread在总控这里配置
    EXTEND_SPREAD=0.30*0.01 #基于库存其实可以计算新spread或新spread的1个偏移系数
    TARGET_BASE_PCT=50
    TOLERABLE_RANGE_PCT=5 #目标比例的，上下容忍比。pct。
    
    IS_CALC_AVAILABLE=True #是否计算可用值
    
    #@todo 这层计算都要改为同一种数据类型，比如 Decimal；Decimal与其他基本类型无法计算
    def __init__(self,strategyMaster:SummPMM,refPrice,targetBaseRate=TARGET_BASE_PCT*0.01,extendSpread=EXTEND_SPREAD):
        '''
        refPrice:用来计算库存价值的base-asset的参考价格
        '''
        #当前状态
        self.baseBalance = Decimal("0.0000")
        self.quoteBalance = Decimal("0.0000")
        # 原始spread 这个应该在spread动态算法中
        self.originalBidSpread=SummPMM.bid_spread #类级别spread
        self.originalAskSpread=SummPMM.ask_spread
        
        self.extendSpread=extendSpread
        #
        self.targetBaseRate=targetBaseRate #需要是个百分比浮点数
        self.tolerableRangeRate=InventoryInfo.TOLERABLE_RANGE_PCT*0.01 #base-pct *
        # self.connecter=
        self.strategyMaster:SummPMM=strategyMaster
        self._calcInventory(refPrice)
        
       
    def _calcInventory(self, refPrice:Decimal):
        '''
        获取balance
        计算base以及quote的库存资产信息与比例
        '''
        conn:ConnectorBase=self.strategyMaster.connectors[self.strategyMaster.exchange]
        baseBal=conn.get_balance(self.strategyMaster.BASE_ASSET)
        quoteBal=conn.get_balance(self.strategyMaster.QUOTE_ASSET)
        
        self.baseBalance=Decimal(baseBal)
        self.quoteBalance=Decimal(quoteBal)
        
        # 计算base以及quote的库存资产信息与比例
        self.baseInventoryValue = self.baseBalance * refPrice
        self.quoteInventoryValue = self.quoteBalance
        self.totalInventoryValue = self.baseInventoryValue + self.quoteInventoryValue
        
        self.baseRate = self.baseInventoryValue / self.totalInventoryValue
        self.quoteRate = self.quoteInventoryValue / self.totalInventoryValue

        #
        #可用部分获取与计算，可能会耗费性能或多次访问交易所
        self.baseAvailableValue=None
        self.quoteAvailableValue=None
        self.totalAvailableValue=None
        self.baseAvailableRate=None
        self.quoteAvailableRate=None
        self.totalAvailableRate=None
        if self.IS_CALC_AVAILABLE:
            baseAvailBal=conn.get_available_balance(self.strategyMaster.BASE_ASSET)
            quoteAvailBal=conn.get_available_balance(self.strategyMaster.QUOTE_ASSET)
            
            self.baseAvailableValue=Decimal(baseAvailBal)*refPrice #计算为quote
            self.quoteAvailableValue=Decimal(quoteAvailBal)
            self.totalAvailableValue=self.baseAvailableValue+self.quoteAvailableValue
            
            self.baseAvailableRate=self.baseAvailableValue/self.baseInventoryValue
            self.quoteAvailableRate=self.quoteAvailableValue/self.quoteInventoryValue
            self.totalAvailableRate=self.totalAvailableValue/self.totalInventoryValue
            

    # @property
    # def baseBalance(self):
    #     return self.baseBalance
    # @property
    # def quoteBalance(self):
    #     return self.quoteBalance
    # @property
    # def baseInventoryValue(self):
    #     return self.baseInventoryValue
    # @property
    # def totalInventoryValue(self):
    #     return self.totalInventoryValue
    # @property
    # def quoteInventoryValue(self):
    #     return self.quoteBalance
    # @property
    # def baseRate(self):
    #     return self.baseRate
    # @property
    # def quoteRate(self):
    #     return self.quoteRate


    def getDataframe(self):
        columns = ["Asset", "AssetType", "Value", "Value pct", "Available pct"]
        data = []
        data.append([
            self.strategyMaster.BASE_ASSET,#
            'BASE',#
            float(self.baseInventoryValue),
            f'{self.baseRate:.2%}',
            f'{self.baseAvailableRate:.2%}' if self.baseAvailableRate is not None else 'NaN'
        ])
        data.append([
            self.strategyMaster.QUOTE_ASSET,#
            'QUOTE',#
            float(self.quoteInventoryValue),
            f'{self.quoteRate:.2%}'  ,
            f'{self.quoteAvailableRate:.2%}' if self.quoteAvailableRate is not None else 'NaN'
        ])
        data.append([
            'Total',#
            'ALL',#
            float(self.totalInventoryValue),
            f'{(self.quoteRate+self.baseRate):.2%}'  ,
            f'{self.totalAvailableRate:.2%}' if self.totalAvailableRate is not None else 'NaN'
        ])
        df = pd.DataFrame(data=data, columns=columns)
        
        return df
        pass
    
    # 这两个逻辑其实带有策略逻辑，其实可以考虑统一到 主策略类中
    # 如果是将逻辑分到各类中执行，这里可以作为1个逻辑点
    #todo 下面 ask（sell） ，bid（buy） 幅度计算重写，其实费杠杆类，只要基于base一个来计算即可。 可能需要按照库存比例阶梯增加spread
    def newAskSpread(self):
        #标的（base）资产越少，标的卖的越高；
        if self.baseRate < Decimal(self.targetBaseRate-self.tolerableRangeRate): #标的资产低于阈值下限
            
            # 230122增加，动态计算出spread；根据指数-开根逻辑库存价格换算。
            ##当前prichgrt变化率为0-10为量纲的值。对应库存变化0-50%
            invChg=abs(float(self.baseRate)-self.targetBaseRate)*100
            priChgRt=PriInvTransBySqr.calcPriChgByInvChg(invChg) #使用第1象限，幅度转换逻辑。+-价变化方向，后续转换为spread变化。
            
            # self.ask_skew_active = True
            return self.originalAskSpread+self.extendSpread*priChgRt
        else:
            # self.ask_skew_active = False
            return self.originalAskSpread
        pass
    
    def newBidSpread(self):
        #计价资产越少（标的资产越多），标的买的越低
        if self.quoteRate < Decimal((1-self.targetBaseRate)-self.tolerableRangeRate): #计价资产低于阈值下限
            
            #230122增加，动态计算出spread；根据指数-开根逻辑库存价格换算。
            ##当前prichgrt变化率为0-10为量纲的值。对应库存变化0-50%
            invChg=abs(float(self.baseRate)-self.targetBaseRate)*100
            priChgRt=PriInvTransBySqr.calcPriChgByInvChg(invChg) #使用第1象限，幅度转换逻辑。+-价变化方向，后续转换为spread变化。 #这里还要转为百分数的数值计算，原为小数
            
            # self.bid_skew_active = True
            return self.originalBidSpread+self.extendSpread*priChgRt
        
        else:
            # self.bid_skew_active = False
            return self.originalBidSpread
        pass
    
    

    
    