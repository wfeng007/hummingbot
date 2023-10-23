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
import pandas 



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
    order_amount = 0.01
  
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
    # bollUBQue=deque([], maxlen=10) #上带
    # bollSmaQue= deque([], maxlen=10)
    # bollLBQue= deque([], maxlen=10)#下带
    
    currentCandlesDf=None
    
    #刷新周期
    order_refresh_time = 7 #11
    #本实例中的 创建tick时间戳
    create_timestamp = 0

    #趋势策略，持仓标志,<0空头仓 >0多头仓 0无仓位 #TODO 这里因为现货用简单仓位标志来记录仓位；
    position_mark=0
    liqQue=deque([], maxlen=10)
    # isOrderTracked=False
    isCoolingDown=False
    
    bollingerDf:pandas.DataFrame

    liqLen=20 #离场线参数；可以跟boll中线参数一致
    paraLocked:bool=False #理论上不会并行，以防万一；
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
        summUtilz=reload_module("summ_hbot_okx_utilz")#重新加载，热加载
        
        #生成1个单例的bollingerDf,维持当前周期（period）的布林带信息等其实也可以记录其他信息；
        bollingerDFColumns = ["timestamp", "upperBand", "sma", "lowerBand","liq"]
        self.bollingerDf:pandas.DataFrame=pandas.DataFrame(columns=bollingerDFColumns)

        self.liqPeri=self.liqLen  #默认立场线，均线周期；
        # for connectorName,connector in self.connectors.items():
        #     self.logger().info(f"Connector:{connectorName}")
        
   
    def on_tick(self):
        
        # tick进入执行点，且未加锁（并发锁）状态则执行本次tick 
        if self.create_timestamp <= self.current_timestamp and not self.paraLocked: #如果有还在运行则直接结束
            try:
                self.paraLocked=True #加锁

                #获取当前K图信息；
                candlesDf=summUtilz.getCandlesDf(pairName=f"{self.BASE_ASSET}-{self.QUOTE_ASSET}", period="1m",limit=21)
                self.currentCandlesDf=candlesDf 
                
                #
                # 布林带；
                # 以收盘价建立当前布林带信息
                # 提取收盘价
                closes = [float(closeP) for closeP in self.currentCandlesDf['close']]
                
                upper_band, sma, lower_band=self.calc_bollinger_bands(closes,n=20 if len(closes)>= 20 else len(closes) )
                #
                #@DEPRECATED 不再使用 有变化则塞入Que；
                # if len(self.bollUBQue)<=0 or abs(self.bollUBQue[-1]-upper_band)>0.000001:
                #     self.bollUBQue.append(upper_band)
                # if len(self.bollSmaQue)<=0 or abs(self.bollSmaQue[-1]-sma)>0.000001:
                #     self.bollSmaQue.append(sma)
                # if len(self.bollLBQue)<=0 or abs(self.bollLBQue[-1]-lower_band)>0.000001:
                #     self.bollLBQue.append(lower_band)

                #基于当前candles最后一行时间戳判断
                nowTs=candlesDf.iloc[-1]['timestamp']#使用candledf中的类型
                isNewCandlePeriod:bool=False #"timestamp", "upperBand", "sma", "lowerBand"
                
                #根据是否candle新周期执行；
                if nowTs not in self.bollingerDf['timestamp'].values:
                    # 如果不存在,判单为candle新周期了
                    # 创建一个新的 Series，写入bollingerDf
                    # 
                    new_row = pandas.Series({'timestamp': nowTs, 'upperBand': upper_band,'sma':sma,'lowerBand':lower_band,'liq':None})

                    # self.bollingerDf = self.bollingerDf.append(new_row, ignore_index=True) #append方法不好用了
                    # series需要转为df并转方向
                    # self.bollingerDf=pandas.concat([self.bollingerDf,pd.DataFrame([new_row])], ignore_index=True,axis=0)
                    self.bollingerDf=pandas.concat([self.bollingerDf,new_row.to_frame().T], ignore_index=True,axis=0)

                    isNewCandlePeriod=True
                    # self.log_with_clock(logging.INFO, f"new Candle Period! isNewCandlePeriod:{isNewCandlePeriod}")
                else:
                    # 如果已存在，根据条件设置
                    # self.bollingerDf.loc[self.bollingerDf['timestamp'] == nowTs] = new_row #df 的行不能直接设置series，本身不一样；
                    self.bollingerDf.loc[self.bollingerDf['timestamp'] == nowTs,["upperBand","sma","lowerBand"]] \
                        = [upper_band,sma,lower_band]
                    isNewCandlePeriod=False
                
                #只保留10个周期
                if len(self.bollingerDf) > 10: 
                    # self.bollingerDf.drop(self.bollingerDf.index[0], inplace=True)
                    catP=len(self.bollingerDf)-10
                    self.bollingerDf=self.bollingerDf.iloc[catP:]
                # self.log_with_clock(logging.INFO, f"bolling is ok!") 调试


                #
                # 根据参考价计算操作信号；
                #
                ref_price = self.connectors[self.exchange]\
                    .get_price_by_type(self.trading_pair, self.price_source) #注意ref_price 这里得到是Decimal 类型

                proposalLs:List[OrderCandidate] = []

                #冷却状态是否恢复
                #回到bb带中则冷却结束 @TODO 冷却恢复的条件可以更多,可以扩展。
                #等到下一个candle周期后再恢复
                if self.isCoolingDown and (ref_price<upper_band and ref_price>lower_band) and isNewCandlePeriod:
                    self.isCoolingDown=False

                #
                #
                #入场开仓信号计算与开仓
                opSi=self._calcOpenSignal(ref_price,closes,upper_band,sma,lower_band)
                if opSi>0:
                    buy_price= None  #下单时使用bestprice
                    buy_order = self._createOrderCandi(order_side=TradeType.BUY,price=buy_price) 
                    proposalLs.append(buy_order)
                elif opSi<0:
                    sell_price = None  #下单时使用bestprice
                    sell_order =self._createOrderCandi(order_side=TradeType.SELL,price=sell_price) 
                    proposalLs.append(sell_order)  
                else: #opSi==0 
                    pass
                


                # @TODO 增加持仓情况的升级版，仓位管理模块化；
                # @TODO 整体以仓位状态为基础判断信号，来进行入离场操作的主流程实现。
                # 
                #离场线计算，离场均线是1某个周期均线的计算值；
                #
                #没有持仓，则为默认周期均线；
                #有持仓，则每经过1单位周期，则离周期-1来计算均线；即随着持仓递减；

                liqLen=20 #离场线参数；可以跟boll中线参数一致
                if  self.position_mark==0:
                    self.liqPeri=liqLen
                elif self.position_mark != 0: #根据candle周期来计算判断
                    #
                    # 是否是同一个candle周期（而不是hbot的tick周期），其实时间短也好。
                    # 简单算法：
                    # self.liqPeri=self.liqPeri-1
                    # 
                    if isNewCandlePeriod: #每过1个candle周期时-1
                        self.liqPeri=self.liqPeri-1

                    #
                    # @TODO BB带中也需要一定量的，liqPeri缩小，即离场线接近当前价格；或周期线缩小?
                    #
                    #
                    #改用更保障盈利逻辑，如果是持多仓，价格高于upper_band 或
                    #   如果是持空仓，价格低于lower_band  则更快速收敛离场线
                    if (self.position_mark > 0 and ref_price > upper_band) \
                        or (self.position_mark < 0 and ref_price < lower_band):
                        self.liqPeri=self.liqPeri-1 #因为7为周期很容易到大上限；

                    #如果持仓且价格在通道内部则不调整离场线；
                    self.liqPeri=max(self.liqPeri,2)
                    
                #    
                #离场移动平均线值，这里算法与bolling的sma没有用同一个，其实应该同一个算法实现；保证一致性；
                liqMeanV=candlesDf.iloc[0:self.liqPeri]['close'].mean()  #candlesDf是时间是从大（新）->小（旧）排序
                # self.log_with_clock(logging.INFO, f"liqMeanV: {liqMeanV}")
                #有变化则放入说明candles已经变化
                # if len(self.liqQue)<=0 or abs(self.liqQue[-1]-liqMeanV)>0.000001: 
                #     self.liqQue.append(liqMeanV)

                self.bollingerDf.loc[self.bollingerDf['timestamp'] == nowTs,["liq"]] = [liqMeanV]
                

                #
                #离场平仓动作；滑单用bestAsk/bid okx只能用limit;
                #持多单时，(自适应出场均线低于布林通道上轨)，且价格下破自适应出场均线，平多；
                # if self.position_mark > 0 and liqMeanV < upper_band and  ref_price< liqMeanV: # ++ and liqMeanV>sma?
                if self.position_mark > 0 and ref_price< liqMeanV: 
                    # 这里卖单替代平多
                    sell_price = None #下单时使用bestprice
                    sell_order = self._createOrderCandi(order_side=TradeType.SELL,price=sell_price) 
                    proposalLs.append(sell_order)   
                # 持空单时，(自适应出场均线高于布林通道下轨)，且价格上破自适应出场均线，平空；
                # elif self.position_mark < 0 and liqMeanV > lower_band and  ref_price> liqMeanV:
                elif self.position_mark < 0 and  ref_price> liqMeanV:
                    # 这里买单替代平空
                    buy_price=None #下单时使用bestprice
                    buy_order = self._createOrderCandi(order_side=TradeType.BUY,price=buy_price) 
                    proposalLs.append(buy_order)
                
                #离场之后可能需要考虑冷却期，因为修改了算法。可能在bb突破状态下止盈。
            
                
                #实际下单 这里已有订单则；
                self.orderTracking.clearNoBaseTracking() #清理其实已经非跟踪订单
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
                #释放锁
                self.paraLocked=False
                
        elif self.create_timestamp <= self.current_timestamp and self.paraLocked:
            self.log_with_clock(logging.INFO, f"tick-Thread locked,self.paraLocked:{self.paraLocked}!")

        pass
    
    #@TODO-架构 最后的3个参数之后抽象放入通用参数或放入属性df，closes最好直接用candles
    def _calcOpenSignal(self,ref_price:Decimal,closes:List,upper_band, sma, lower_band): 
        '''
            根据信息比如candle或candle的结束价计算入场信号，确定是否要入场；
            return  0 表示没有建仓信号
                    1/>0 表示建多仓信号
                    -1/<0 表示建空仓信号
            
            这里使用了rocThre来确定是否要建仓的另个基本过滤；前某个周期的close与当前参考价格比较来确定入场；
            @TODO 之后可以考虑对rocThre的计算可以动态比较，而不是rocThre>0 的0才是动态阈值/门限；
        '''

        #
        #入场信号
        #
        #
        rocLen=2 #入场过滤器参数
        #@TODO 入场过滤器计算，之后替换为更合理的入场信号；
        rocThre=float(ref_price)-closes[-rocLen] #计算过滤器值，正负方向，大小为强度；

        #
        #开仓逻辑与信号处理 
        #
        #无多头持仓，且roc过滤器为正，突破上线，开多仓；不在冷却中；
        if self.position_mark == 0 and not self.isCoolingDown and rocThre>0 and ref_price>upper_band:
            return 1
        #无空头仓，且roc过滤器为负，突破下线，开空仓；不在冷却中；
        elif self.position_mark == 0 and not self.isCoolingDown and rocThre<0 and ref_price<lower_band:
            return -1
        
        return 0

    def _createOrderCandi(self,order_side:TradeType,price:Decimal=None):
        return OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,\
                order_side=order_side, amount=Decimal(self.order_amount), price=price)
    
    def format_status(self)->str:
        '''
        展示BB数据，以及布林强盗算法中的离场移动均线；
        '''
        lines = []
        try:
            if not self.ready_to_trade:
                return "Market connectors are not ready."
            # warning_lines = []
            # warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))
            
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
            lines.extend(["仓位情况标志,position_mark:"+str(self.position_mark)])
            lines.extend(["是否冷却中,isCoolingDown:"+str(self.isCoolingDown)])
            lines.extend([" "])

            # @DEPRECATED 
            # lines.extend(["BB&Liq布林与离场线:"]+["bollUBQue:"+str(self.bollUBQue)])
            # lines.extend(["bollSmaQue:"+str(self.bollSmaQue)]) 
            # lines.extend(["bollLBQue:"+str(self.bollLBQue)]) 
            # lines.extend(["liqQue:"+str(self.liqQue)])
            # lines.extend([" "])

            bolDf=self.bollingerDf.copy()
            bolDf=bolDf.iloc[-3:]
            lines.extend(["bolling信息[1m-bolling]-3:"] + ["    " + line for line in bolDf.to_string(index=False).split("\n")])
            lines.extend([" "])
            
            candlesDf=self.currentCandlesDf
            candlesDf=candlesDf.iloc[0:3]
            lines.extend(["1分钟k线信息[1m-kline]:"] + ["    " + line for line in candlesDf.to_string(index=False).split("\n")])
            lines.extend([" "])
            # return "\n".join(lines)
        except Exception as e :
            self.log_with_clock(logging.ERROR, f"except on on_tick:{str(e)}")
            self.logger().exception(f"except on on_tick:{e}")
        finally:
            return "\n".join(lines)
            pass
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


        if self.position_mark == 0 :# 开仓，并标记 
            if event.trade_type == TradeType.BUY: self.position_mark= 1 
            elif event.trade_type == TradeType.SELL: self.position_mark= -1 
            if self.position_mark!=0: self.log_with_clock(logging.INFO, f"开仓，position_mark为：{self.position_mark}")
            else:self.log_with_clock(logging.ERROR,\
                    f"开仓后设置标志异常，没匹配到TradeType：{event.trade_type}，position_mark为：{self.position_mark}")
        #self.position_mark!=0 平仓，并标记
        elif self.position_mark != 0 :
            self.position_mark= 0
            self.isCoolingDown=True #平仓后进入冷却期；
            self.log_with_clock(logging.INFO, f"平仓，position_mark为：{self.position_mark}")

        #
        # 貌似有时候这个ignore没有成功执行；导致策略层跟踪器中还是有对应Order_id; 
        # @FIXME  ***或者由于下单与成交太接近，成单回调did_fill 早于 创建订单回调did_create，***
        #         ***导致更新状态出错或成单更新后再被设置了orderId，但无法再释放id。***
        #
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